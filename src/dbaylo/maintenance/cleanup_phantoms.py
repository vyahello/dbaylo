"""Remove phantom rows created by browsing commands before the command-cancel fix.

A dialog used to consume a following ``/command`` (or blank text) as its answer, so
just tapping ``/goal`` then ``/goals`` "saved" a goal, and ``/problem`` then
``/medication`` created an empty active concern *and* scheduled the daily check-in.
A phantom is precisely identifiable: its name/target is empty, whitespace-only, or
starts with ``/`` (a command consumed as input). Real goals/concerns/medications are
never blank and never start with a slash.

Deleting phantom concerns can leave the daily check-in reminder pointless (it exists
*iff* an active concern exists), so for every affected user we retire that reminder
when no active concern remains. The running bot rebuilds jobs from these rows on the
next restart (the deploy restarts it; ``reconcile`` self-heals on startup).

    python -m dbaylo.maintenance.cleanup_phantoms --dry-run   # list, delete nothing
    python -m dbaylo.maintenance.cleanup_phantoms             # delete + commit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import reminders
from dbaylo.db import get_session
from dbaylo.db.models import Condition, ConditionStatus, Goal, Medication, Reminder


def is_phantom(value: str | None) -> bool:
    """A value that no real entry would have: blank/whitespace, or a leading ``/``."""
    return not value or not value.strip() or value.lstrip().startswith("/")


@dataclass(frozen=True)
class Phantoms:
    goals: list[Goal]
    conditions: list[Condition]
    medications: list[Medication]

    @property
    def is_empty(self) -> bool:
        return not (self.goals or self.conditions or self.medications)


@dataclass(frozen=True)
class Counts:
    goals: int
    conditions: int
    medications: int
    medication_reminders: int
    checkins_retired: int


async def find_phantoms(session: AsyncSession) -> Phantoms:
    """Collect every phantom goal / concern / medication (read-only)."""
    goals = [g for g in (await session.scalars(select(Goal))).all() if is_phantom(g.target)]
    conditions = [c for c in (await session.scalars(select(Condition))).all() if is_phantom(c.name)]
    medications = [
        m for m in (await session.scalars(select(Medication))).all() if is_phantom(m.name)
    ]
    return Phantoms(goals=goals, conditions=conditions, medications=medications)


async def delete_phantoms(session: AsyncSession) -> Counts:
    """Delete phantom rows (+ a medication's reminders) and retire orphaned check-ins.

    Does not commit — the caller decides. Flushes so counts reflect the work done.
    """
    phantoms = await find_phantoms(session)
    affected_users = {c.user_id for c in phantoms.conditions}

    for goal in phantoms.goals:
        await session.delete(goal)

    medication_reminders = 0
    for medication in phantoms.medications:
        rows = (
            await session.scalars(select(Reminder).where(Reminder.medication_id == medication.id))
        ).all()
        for reminder in rows:
            await session.delete(reminder)
            medication_reminders += 1
        await session.delete(medication)

    for condition in phantoms.conditions:
        await session.delete(condition)
    await session.flush()

    checkins_retired = 0
    for user_id in affected_users:
        remaining = await session.scalar(
            select(func.count())
            .select_from(Condition)
            .where(Condition.user_id == user_id, Condition.status == ConditionStatus.ACTIVE)
        )
        if remaining:
            continue
        checkin = await session.scalar(
            select(Reminder).where(
                Reminder.user_id == user_id,
                Reminder.type == reminders.TYPE_CHECKIN,
                Reminder.active.is_(True),
            )
        )
        if checkin is not None:
            checkin.active = False
            checkins_retired += 1
    await session.flush()

    return Counts(
        goals=len(phantoms.goals),
        conditions=len(phantoms.conditions),
        medications=len(phantoms.medications),
        medication_reminders=medication_reminders,
        checkins_retired=checkins_retired,
    )


def _print_report(phantoms: Phantoms) -> None:
    print("Phantom rows found:")
    print(f"  goals:       {len(phantoms.goals)}")
    for g in phantoms.goals:
        print(f"    - Goal#{g.id} user={g.user_id} target={g.target!r}")
    print(f"  conditions:  {len(phantoms.conditions)}")
    for c in phantoms.conditions:
        print(f"    - Condition#{c.id} user={c.user_id} status={c.status.value} name={c.name!r}")
    print(f"  medications: {len(phantoms.medications)}")
    for m in phantoms.medications:
        print(f"    - Medication#{m.id} user={m.user_id} name={m.name!r}")


async def _run(*, dry_run: bool) -> int:
    async with get_session() as session:
        phantoms = await find_phantoms(session)
        _print_report(phantoms)
        if phantoms.is_empty:
            print("\nNothing to clean.")
            return 0
        if dry_run:
            print("\n[dry-run] nothing deleted.")
            return 0
        counts = await delete_phantoms(session)
        await session.commit()
        print(
            f"\nDeleted: {counts.goals} goals, {counts.conditions} conditions, "
            f"{counts.medications} medications (+{counts.medication_reminders} reminders); "
            f"retired {counts.checkins_retired} now-pointless check-in reminder(s)."
        )
        print("Restart the bot so the scheduler drops the retired job(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.maintenance.cleanup_phantoms")
    parser.add_argument("--dry-run", action="store_true", help="list phantom rows; delete nothing")
    args = parser.parse_args(argv)
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
