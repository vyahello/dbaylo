"""Coordinator: keep the live scheduler in sync with concern/medication/reminder state.

The conditional-check-in invariant lives here: a check-in reminder (and its job)
exists **iff** the user has at least one active concern. Adding the first concern
schedules it; resolving the last one removes it. Medication add schedules one job per
dose time; turning a medication off removes them all. Everything runs against the
**live** :class:`ReminderScheduler`, so changes take effect without a restart.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import concerns, health, medications, reminders
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db.models import Condition, Medication, Reminder, User


async def _active_checkin(session: AsyncSession, user_id: int) -> Reminder | None:
    result: Reminder | None = await session.scalar(
        select(Reminder).where(
            Reminder.user_id == user_id,
            Reminder.type == reminders.TYPE_CHECKIN,
            Reminder.active.is_(True),
        )
    )
    return result


async def reconcile_checkin(
    session: AsyncSession, *, user: User, scheduler: ReminderScheduler
) -> None:
    """Make the live check-in match ``health.should_have_checkin``: schedule it when there's
    something to check in about, retire it when there isn't. Idempotent — safe to call any time."""
    existing = await _active_checkin(session, user.id)
    wanted = await health.should_have_checkin(session, user.id, today=date.today())
    if wanted:
        # Create OR retime (the configured hour may have changed), then (re)schedule — the live
        # scheduler replaces the job, so a retimed schedule takes effect immediately.
        reminder = await reminders.ensure_checkin_reminder(session, user=user)
        await session.flush()
        scheduler.schedule(reminder)
    elif existing is not None:
        reminder_id = existing.id
        await reminders.deactivate(session, existing)
        scheduler.unschedule(reminder_id)


async def add_problem(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    scheduler: ReminderScheduler,
    report_id: int | None = None,
) -> Condition:
    """Add an active concern; (re)schedule the daily check-in if warranted."""
    condition = await concerns.add_active(session, user=user, name=name, report_id=report_id)
    await reconcile_checkin(session, user=user, scheduler=scheduler)
    return condition


async def dismiss_problem(
    session: AsyncSession, *, user: User, name: str, scheduler: ReminderScheduler
) -> Condition:
    """Wave off an AI-proposed finding ("Не турбує"): remember it as DISMISSED so it isn't
    re-proposed, then reconcile — if it was the last thing keeping the data-driven check-in alive,
    the check-in is retired."""
    condition = await concerns.dismiss(session, user=user, name=name)
    await reconcile_checkin(session, user=user, scheduler=scheduler)
    return condition


async def resolve_problem(
    session: AsyncSession, *, user_id: int, condition_id: int, scheduler: ReminderScheduler
) -> Condition | None:
    """Resolve a concern; retire the check-in only if nothing else (concern or data flag) warrants
    one."""
    condition = await concerns.resolve(session, condition_id)
    if condition is None or condition.user_id != user_id:
        return condition
    user = await session.get(User, user_id)
    if user is not None:
        await reconcile_checkin(session, user=user, scheduler=scheduler)
    return condition


async def restore_problem(
    session: AsyncSession, *, user_id: int, condition_id: int, scheduler: ReminderScheduler
) -> Condition | None:
    """Undo a wrongly-tapped ✖ ("повернути під нагляд"): drop the DISMISSED row so the finding is
    proposed again, then reconcile — a restored current flag re-enables the data-driven check-in."""
    condition = await concerns.undismiss(session, user_id=user_id, condition_id=condition_id)
    if condition is None:
        return None
    user = await session.get(User, user_id)
    if user is not None:
        await reconcile_checkin(session, user=user, scheduler=scheduler)
    return condition


async def reopen_problem(
    session: AsyncSession, *, user_id: int, condition_id: int, scheduler: ReminderScheduler
) -> Condition | None:
    """Re-open a RESOLVED concern ("знову під нагляд") from the «✔️ Вирішені» archive: set it ACTIVE
    again, then reconcile so the daily check-in turns back on for it."""
    condition = await concerns.reopen(session, user_id=user_id, condition_id=condition_id)
    if condition is None:
        return None
    user = await session.get(User, user_id)
    if user is not None:
        await reconcile_checkin(session, user=user, scheduler=scheduler)
    return condition


async def add_medication(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    times: list[time],
    scheduler: ReminderScheduler,
    dose: str | None = None,
    source_file: str | None = None,
    course: str | None = None,
    until: date | None = None,
) -> tuple[Medication, list[Reminder]]:
    medication, created = await medications.add_medication(
        session,
        user=user,
        name=name,
        times=times,
        dose=dose,
        source_file=source_file,
        course=course,
        until=until,
    )
    for reminder in created:
        scheduler.schedule(reminder)
    return medication, created


async def add_repeat_lab(
    session: AsyncSession,
    *,
    user: User,
    run_at: datetime,
    label: str,
    scheduler: ReminderScheduler,
    report_id: int | None = None,
) -> Reminder:
    reminder = await reminders.create_repeat_lab(
        session, user=user, run_at=run_at, label=label, report_id=report_id
    )
    scheduler.schedule(reminder)
    return reminder


async def add_consult_reminder(
    session: AsyncSession,
    *,
    user: User,
    run_at: datetime,
    label: str,
    scheduler: ReminderScheduler,
) -> tuple[Reminder, bool]:
    """Create + live-schedule a one-off reminder agreed during a consultation. Returns
    ``(reminder, created)``: if an identical active one already exists, it is reused (``created`` is
    False) so asking twice never leaves two identical reminders."""
    existing = await reminders.find_active_consult(
        session, user_id=user.id, schedule=reminders.once(run_at), payload=label
    )
    if existing is not None:
        return existing, False
    reminder = await reminders.create_consult_reminder(
        session, user=user, run_at=run_at, label=label
    )
    scheduler.schedule(reminder)
    return reminder, True


async def turn_off_reminder(
    session: AsyncSession, *, reminder: Reminder, scheduler: ReminderScheduler
) -> None:
    reminder_id = reminder.id
    await reminders.deactivate(session, reminder)
    scheduler.unschedule(reminder_id)


async def turn_off_medication(
    session: AsyncSession, *, medication_id: int, scheduler: ReminderScheduler
) -> None:
    ids = await reminders.deactivate_medication(session, medication_id)
    scheduler.unschedule_many(ids)


async def turn_off_course(
    session: AsyncSession, *, user_id: int, course: str, scheduler: ReminderScheduler
) -> None:
    """Turn off the reminders of EVERY medication in a prescription (course) at once — the records +
    doses stay (rail #1), only the jobs stop. The meds fired separately; they retire together."""
    for med in await medications.list_by_course(session, user_id=user_id, course=course):
        await turn_off_medication(session, medication_id=med.id, scheduler=scheduler)


async def delete_course(
    session: AsyncSession, *, user_id: int, course: str, scheduler: ReminderScheduler
) -> str | None:
    """PERMANENTLY delete a prescription: every med in the course + all their reminders (rows+jobs).
    Returns the shared photo path to unlink — or ``None`` if any remaining med still references it
    (so a shared file is never deleted out from under another record)."""
    meds = await medications.list_by_course(session, user_id=user_id, course=course)
    file = next((m.source_file for m in meds if m.source_file), None)
    for med in meds:
        await delete_medication_reminders(session, medication_id=med.id, scheduler=scheduler)
        await session.delete(med)
    await session.flush()
    if file is not None:
        remaining = await session.scalar(
            select(func.count()).select_from(Medication).where(Medication.source_file == file)
        )
        if remaining:
            return None
    return file


async def restore_course(
    session: AsyncSession,
    *,
    user_id: int,
    course: str,
    scheduler: ReminderScheduler,
    today: date,
) -> None:
    """Re-activate a finished prescription (from the archive): every med's soft-deleted reminders go
    live again. A med whose term already passed has its ``until`` cleared (the doctor extended it →
    open-ended, so it does not immediately re-expire); a term still in the future is kept."""
    for med in await medications.list_by_course(session, user_id=user_id, course=course):
        if med.until is not None and med.until < today:
            med.until = None
        for reminder in await reminders.reactivate_medication(session, med.id):
            scheduler.schedule(reminder)


async def delete_reminder(
    session: AsyncSession, *, reminder: Reminder, scheduler: ReminderScheduler
) -> None:
    """Hard-delete a reminder the user removed from the list, and unschedule its job."""
    reminder_id = reminder.id
    await reminders.delete(session, reminder)
    scheduler.unschedule(reminder_id)


async def delete_medication_reminders(
    session: AsyncSession, *, medication_id: int, scheduler: ReminderScheduler
) -> None:
    """Hard-delete all of a medication's reminders (the user removed them from the list) and
    unschedule their jobs; the Medication record stays."""
    ids = await reminders.delete_medication_reminders(session, medication_id)
    scheduler.unschedule_many(ids)
