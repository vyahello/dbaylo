"""Medications — user-entered record-keeping that drives recurring reminders.

Rail #1: the bot never suggests or selects a drug or a dose. The user types the
medication name and the dose *times* (from their doctor's prescription); we store a
:class:`Medication` record and create one recurring :class:`Reminder` per time. The
reminder names the medication and shows the doctor's prescribed AMOUNT as a record
(:func:`safe_dose_record`) — never a dose *directive*: a dosing verb or a frequency is
refused, so it never reads as Дбайло ordering a dose (the amount-as-record boundary).

Turning a medication off deactivates **all** of its reminders (one per time), so no
orphaned jobs keep firing.
"""

from __future__ import annotations

import re
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import reminders
from dbaylo.db.models import Medication, Reminder, User
from dbaylo.triage.safety import contains_dose_verb

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
# A per-intake amount for record-keeping (rail #1 allows storing what a doctor prescribed). Shown in
# the reminder as a doctor-attributed amount record. e.g. "2 таблетки", "500 мг", "10 крапель".
_DOSE_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:табл\w*|таб\b|капсул\w*|капс\b|драже|саше|"
    r"мг|мкг|г\b|мл|крапл\w*|кап\b|од\b|мо\b)",
    re.IGNORECASE,
)
# Which dose tokens are a COUNT + dosage FORM ("1 таблетка") vs a STRENGTH ("5 мг"). The count/form
# is "how many to swallow" — shown first in the record because that is what the owner asked to see.
_FORM_TOKEN_RE = re.compile(r"табл|таб\b|капсул|капс\b|драже|саше|крапл|кап\b", re.IGNORECASE)

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
    """The per-intake amount for RECORD-KEEPING — every count/form/strength token the doctor wrote,
    de-duplicated and joined ("1 таблетка", "5 мг", both when present → "1 таблетка · 5 мг"), or
    ``None``. Stored on ``Medication.dose`` (rail #1) and shown in the reminder as a
    doctor-attributed record so the user need not remember the script. The COUNT/FORM is listed
    first (what to swallow), then the STRENGTH. A daily frequency ("3 рази") and a duration
    ("10 днів") are NOT captured here — they drive the schedule / expiry, not the amount."""
    seen: list[str] = []
    keys: set[str] = set()
    for m in _DOSE_RE.finditer(text):
        token = re.sub(r"\s+", " ", m.group(0).strip())
        if token.casefold() not in keys:
            keys.add(token.casefold())
            seen.append(token)
    if not seen:
        return None
    forms = [t for t in seen if _FORM_TOKEN_RE.search(t)]
    strengths = [t for t in seen if not _FORM_TOKEN_RE.search(t)]
    return " · ".join(forms + strengths)


def safe_dose_record(dose: str | None) -> str | None:
    """The doctor's per-intake AMOUNT ("1 таблетка", "1 таблетка · 5 мг") for the reminder — a
    doctor-attributed RECORD so the user need not remember the script. The COUNT, dosage FORM and
    STRENGTH are kept (the owner wants to see exactly how much to take); a dosing VERB ("приймай")
    or a daily FREQUENCY ("3 рази на день") is refused, so the line can never read as Дбайло
    *ordering* a dose — the reminder still frames it as the doctor's instruction (rail #1, the
    amount-as-record boundary). ``None`` when no real amount is present or the text reads as a
    directive (defense in depth, on top of the reminder renderer's own re-check)."""
    if not dose:
        return None
    cleaned = re.sub(r"\s+", " ", dose).strip(" .;,·")
    if not cleaned or contains_dose_verb(cleaned) is not None:
        return None
    if _FREQ_NUM_RE.search(cleaned) or _FREQ_ABBR_RE.search(cleaned):
        return None
    return cleaned if _DOSE_RE.search(cleaned) else None


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


# How long to take a med — number + unit ("3 міс.", "10 днів", "2 тижні", "1 рік").
_DURATION_RE = re.compile(
    r"(\d+)\s*(дн|день|діб|доб|тижн|тиж|нед|міс|мiс|рок|рік|рік|р\.)",  # noqa: RUF001 (Cyrillic і/i)
    re.IGNORECASE,
)
# An explicit end date — "до 27.07" / "до 27.07.2026".
_END_DATE_RE = re.compile(r"до\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?")


def _add_months(d: date, months: int) -> date:
    """Add calendar months to a date, clamping the day to the target month's length."""
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    day = min(d.day, (date(year + month // 12, month % 12 + 1, 1) - date(year, month, 1)).days)
    return date(year, month, day)


def course_end(start: date, duration_text: str | None) -> date | None:
    """The LAST day to take a med, counted from ``start``, from a duration phrase ("3 міс.",
    "10 днів", "2 тижні", "1 рік"). ``None`` when no duration is expressed — an open-ended course
    the bot never auto-expires. The doctor sets the term; the bot just stops reminding after it."""
    if not duration_text:
        return None
    if explicit := _explicit_end(duration_text, start):
        return explicit
    m = _DURATION_RE.search(duration_text.casefold())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit.startswith(("дн", "ден", "діб", "доб")):
        return _add_days(start, n)
    if unit.startswith(("тиж", "нед")):
        return _add_days(start, n * 7)
    if unit.startswith(("міс", "мiс")):  # noqa: RUF001
        return _add_months(start, n)
    return _add_months(start, n * 12)  # years


def _add_days(d: date, days: int) -> date:
    from datetime import timedelta

    return d + timedelta(days=days)


def _explicit_end(text: str, start: date) -> date | None:
    """An explicit "до DD.MM[.YYYY]" end date; a year-less past date rolls to next year."""
    m = _END_DATE_RE.search(text.casefold())
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    if m.group(3):
        year = int(m.group(3))
        year += 2000 if year < 100 else 0
    else:
        year = start.year
    try:
        end = date(year, month, day)
    except ValueError:
        return None
    if not m.group(3) and end < start:  # no year given and the date is past -> next year
        end = date(year + 1, month, day)
    return end


async def add_medication(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    times: list[time],
    dose: str | None = None,
    source_file: str | None = None,
    course: str | None = None,
    until: date | None = None,
    content_hash: str | None = None,
) -> tuple[Medication, list[Reminder]]:
    """Record the medication and create one daily reminder per dose time.

    ``dose`` is optional RECORD-KEEPING of the prescribed amount (e.g. captured from a prescription
    photo) — stored on the :class:`Medication` (rail #1 allows storing what a doctor prescribed) but
    NEVER placed in the reminder text. ``source_file`` is the original prescription image/PDF the
    med was read from, kept so the user can re-open it. ``course`` groups meds from one prescription
    under a label. ``until`` is the last day to take it (from the printed duration) — the scheduler
    retires it after. All are ``None`` for a standalone, open-ended manually-entered medication.
    """
    medication = Medication(
        user_id=user.id,
        name=name.strip(),
        dose=(dose or None),
        schedule=", ".join(t.strftime("%H:%M") for t in times),
        source_file=(source_file or None),
        course=(course or None),
        until=until,
        content_hash=(content_hash or None),
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


async def find_by_content_hash(
    session: AsyncSession, *, user_id: int, content_hash: str
) -> Medication | None:
    """An existing medication created from the SAME prescription photo bytes (any status), so the
    same script dropped twice doesn't make a second course. A DELETED med leaves no row → not a
    duplicate (re-adding allowed), mirroring the lab duplicate guard."""
    found: Medication | None = await session.scalar(
        select(Medication)
        .where(Medication.user_id == user_id, Medication.content_hash == content_hash)
        .order_by(Medication.id)
        .limit(1)
    )
    return found


async def list_by_course(session: AsyncSession, *, user_id: int, course: str) -> list[Medication]:
    """Every medication in one prescription (course), in creation order."""
    rows = await session.scalars(
        select(Medication)
        .where(Medication.user_id == user_id, Medication.course == course)
        .order_by(Medication.created_at)
    )
    return list(rows.all())


async def list_medications(session: AsyncSession, *, user_id: int) -> list[Medication]:
    rows = await session.scalars(
        select(Medication).where(Medication.user_id == user_id).order_by(Medication.created_at)
    )
    return list(rows.all())


# Leading dosage-form markers a prescription prints before the drug name ("Т. Буспірон",
# "таб дулоксетин", "К. Симода") — stripped so the SAME drug keys the same across two scripts.
_FORM_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"[тк]\.\s*"  # Т. (таблетки) / К. (капсули), the dotted form abbreviations
    r"|табл?\.?\s+|таблетк\w*\s+|капс?\.?\s+|капсул\w*\s+"
    r"|драже\s+|саше\s+|р-?н\.?\s+|розчин\s+|сусп\w*\s+|сироп\w*\s+"
    r"|крем\s+|мазь\s+|гель\s+|свічк\w*\s+"
    r")",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """A medication name reduced to a comparison key: lowercased, leading dosage-form markers ("Т.",
    "таб", "капс") stripped, then all punctuation/whitespace removed. So "Т. Буспірон", "таб
    буспірон" and "Буспірон" all key the same — used to avoid the SAME drug from two different
    prescriptions firing twice."""
    n = name.casefold().strip()
    prev = ""
    while prev != n:  # strip any stacked form markers ("таб капс …")
        prev = n
        n = _FORM_PREFIX_RE.sub("", n).strip()
    return re.sub(r"[^\w]+", "", n)


async def live_normalized_names(session: AsyncSession, *, user_id: int) -> set[str]:
    """Normalized names of medications that currently have ≥1 ACTIVE reminder — the dedup set, so a
    drug already being taken isn't scheduled again from a new prescription (the owner's two
    overlapping scripts both listed Буспірон → one set of reminders, not two)."""
    active = await reminders.active_reminders_for_user(session, user_id=user_id)
    live_ids = {
        r.medication_id
        for r in active
        if r.type == reminders.TYPE_MEDICATION and r.medication_id is not None
    }
    if not live_ids:
        return set()
    meds = await list_medications(session, user_id=user_id)
    return {normalize_name(m.name) for m in meds if m.id in live_ids}
