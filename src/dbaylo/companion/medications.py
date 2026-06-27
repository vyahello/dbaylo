"""Medications — user-entered record-keeping that drives recurring reminders.

Rail #1: the bot never suggests or selects a drug or a dose. The user types the
medication name and the dose *times* (from their doctor's prescription); we store a
:class:`Medication` record and create one recurring :class:`Reminder` per time. The
reminder text names the medication and defers to the doctor — it never carries a dose.

Turning a medication off deactivates **all** of its reminders (one per time), so no
orphaned jobs keep firing.
"""

from __future__ import annotations

import re
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import reminders
from dbaylo.db.models import Medication, Reminder, User

_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
# "N разів/раз" — the number of intakes per day. The number is the one BEFORE "раз", so in
# "2 таблетки 3 рази" the frequency is 3 (the "2" is the per-intake amount, captured as the dose).
_FREQ_NUM_RE = re.compile(r"(\d+)\s*раз", re.IGNORECASE)
# The doctor's abbreviation "N р/д" / "N р/добу" (= N разів на день).
_FREQ_ABBR_RE = re.compile(r"(\d+)\s*р\s*[/\\.]?\s*д", re.IGNORECASE)
# Time-of-day phrases doctors write instead of a count ("зранку", "на ніч", "вранці та ввечері") —
# each named part of the day maps to a sensible clock time; several phrases ⇒ several intakes.
_TIME_OF_DAY: tuple[tuple[tuple[str, ...], tuple[int, int]], ...] = (
    (("натще", "зранк", "вранц", "ранков", "ранку", "ранком", "сніда"), (9, 0)),
    (("обід", "вдень", "удень", "опівдн", "полудень"), (14, 0)),
    (
        (
            "ввечер",
            "увечер",
            "вечір",
            "вечор",
            "вечер",
            "на ніч",
            "вночі",
            "уночі",
            "ноч",
            "перед сном",
            "сном",
            "ніч",
        ),
        (21, 0),
    ),
)
# A per-intake amount for record-keeping (rail #1 allows storing what a doctor prescribed; never in
# a reminder). e.g. "2 таблетки", "500 мг", "10 крапель", "1 капсула".
_DOSE_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:табл\w*|таб\b|капсул\w*|капс\b|драже|саше|"
    r"мг|мкг|г\b|мл|крапл\w*|кап\b|од\b|мо\b)",
    re.IGNORECASE,
)

# Deterministic waking-hours dosing schedules. A doctor prescribes "N разів на день", NOT clock
# times — so the bot spreads the intakes across an ~08:00–22:00 waking day itself.
_DOSING_SCHEDULE: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((9, 0),),
    2: ((9, 0), (21, 0)),
    3: ((8, 0), (14, 0), (20, 0)),
    4: ((8, 0), (12, 0), (16, 0), (20, 0)),
    5: ((8, 0), (11, 0), (14, 0), (17, 0), (20, 0)),
    6: ((8, 0), (11, 0), (14, 0), (17, 0), (20, 0), (22, 0)),
}
MAX_PER_DAY = 6


def parse_times(text: str) -> list[time]:
    """Extract dose times (HH:MM) from free text, de-duplicated, in order."""
    seen: set[time] = set()
    out: list[time] = []
    for match in _TIME_RE.finditer(text):
        t = time(int(match.group(1)), int(match.group(2)))
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def parse_frequency(text: str) -> int | None:
    """Intakes per day from free text — "N разів/раз на день", "N р/д", "двічі", "тричі", or a bare
    "раз на день". ``None`` when no count is expressed. The doctor's instruction is the frequency;
    the bot, not the user, picks the clock times."""
    low = text.casefold()
    if m := _FREQ_NUM_RE.search(low):
        n = int(m.group(1))
        return n if 1 <= n <= MAX_PER_DAY else None
    if m := _FREQ_ABBR_RE.search(low):  # "3 р/д"
        n = int(m.group(1))
        return n if 1 <= n <= MAX_PER_DAY else None
    if "двічі" in low:
        return 2
    if "тричі" in low:
        return 3
    if re.search(r"\bраз\b", low) and re.search(r"(день|добу|щодня|щодоби)", low):
        return 1  # "раз на день" with no number
    return None


def times_of_day(text: str) -> list[time]:
    """The clock times named by part-of-day phrases ("зранку" → 09:00, "на ніч" → 21:00), in order,
    de-duplicated. ``[]`` when none are mentioned. Lets "вранці та ввечері" become two intakes."""
    low = text.casefold()
    out: list[time] = []
    for words, (h, m) in _TIME_OF_DAY:
        if any(w in low for w in words):
            t = time(h, m)
            if t not in out:
                out.append(t)
    return sorted(out)


def distribute_times(per_day: int) -> list[time]:
    """Spread ``per_day`` intakes across waking hours (deterministic), so the user / doctor need
    only say "N разів на день" and the bot schedules the times. Clamped to 1..``MAX_PER_DAY``."""
    per_day = max(1, min(MAX_PER_DAY, per_day))
    return [time(h, m) for h, m in _DOSING_SCHEDULE[per_day]]


def parse_dose(text: str) -> str | None:
    """The per-intake amount as free text for RECORD-KEEPING ("2 таблетки", "500 мг"), or ``None``.
    Stored on ``Medication.dose`` (rail #1) — never shown in a reminder."""
    m = _DOSE_RE.search(text)
    return m.group(0).strip() if m else None


def times_from_text(text: str) -> list[time]:
    """Best dosing times from one free-text instruction, in priority order: explicit "HH:MM" →
    part-of-day phrases ("зранку"/"на ніч") → a frequency ("3 рази на день", "3 р/д") spread across
    the day. ``[]`` when nothing usable is found. The clock times are the bot's job, not the user's.
    """
    if explicit := parse_times(text):
        return explicit
    if tod := times_of_day(text):
        return tod
    freq = parse_frequency(text)
    return distribute_times(freq) if freq is not None else []


def resolve_schedule(text: str) -> tuple[list[time], str | None]:
    """Turn one free-text dosing answer into (times, dose) — see :func:`times_from_text`. Returns
    ``([], dose)`` when no schedule could be read, so the caller re-asks. Dose is record-keeping."""
    return times_from_text(text), parse_dose(text)


async def add_medication(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    times: list[time],
    dose: str | None = None,
    source_file: str | None = None,
    course: str | None = None,
) -> tuple[Medication, list[Reminder]]:
    """Record the medication and create one daily reminder per dose time.

    ``dose`` is optional RECORD-KEEPING of the prescribed amount (e.g. captured from a prescription
    photo) — stored on the :class:`Medication` (rail #1 allows storing what a doctor prescribed) but
    NEVER placed in the reminder text. ``source_file`` is the original prescription image/PDF the
    med was read from, kept so the user can re-open it. ``course`` groups meds from one prescription
    under a label. Both are ``None`` for a standalone manually-entered medication.
    """
    medication = Medication(
        user_id=user.id,
        name=name.strip(),
        dose=(dose or None),
        schedule=", ".join(t.strftime("%H:%M") for t in times),
        source_file=(source_file or None),
        course=(course or None),
    )
    session.add(medication)
    await session.flush()

    created: list[Reminder] = []
    for t in times:
        reminder = await reminders.create_reminder(
            session,
            user=user,
            type=reminders.TYPE_MEDICATION,
            schedule=f"cron:{t.minute} {t.hour} * * *",
            payload=medication.name,
            medication_id=medication.id,
        )
        created.append(reminder)
    return medication, created


async def list_medications(session: AsyncSession, *, user_id: int) -> list[Medication]:
    rows = await session.scalars(
        select(Medication).where(Medication.user_id == user_id).order_by(Medication.created_at)
    )
    return list(rows.all())
