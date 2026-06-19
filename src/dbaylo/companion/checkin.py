"""Daily check-in: prompt, parse, route symptoms to triage, persist.

The evening prompt is sent by the scheduler. The user's free-text answer is parsed
leniently (sleep / water / mood / training) and any symptom mention is routed to
the **deterministic** triage engine — the LLM never makes that escalation call.

"One gentle reminder, never nag": a single follow-up checks whether a check-in
arrived today; :func:`should_send_nudge` returns True at most once, and the
scheduler sends exactly one nudge. No streak/guilt language anywhere.

``python -m dbaylo.companion.checkin --dry-run`` prints the prompt without sending.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.companion import callbacks, concerns
from dbaylo.companion.symptoms import detect_symptoms
from dbaylo.db.models import CheckIn, User
from dbaylo.safety import screen
from dbaylo.triage.safety import assert_safe_output
from dbaylo.triage.types import Symptom

# A proactive message: text + optional inline buttons as (label, callback_data) pairs
# (kept aiogram-free; the bot layer turns the pairs into a real keyboard).
ProactiveMessage = tuple[str, list[tuple[str, str]] | None]

_NUM = r"(\d+(?:[.,]\d+)?)"
_TRAINING_HINTS = (
    "трену",
    "біг",
    "пробіж",
    "зал",
    "йог",
    "ходив",
    "ходила",
    "прогулянк",
    "присід",
    "плав",
    "велосипед",
    "качав",
)


def build_prompt() -> str:
    """The gentle evening check-in prompt (safety-checked)."""
    return assert_safe_output(locale.CHECKIN_PROMPT)


async def checkin_messages(
    session: AsyncSession, *, user_id: int, now: datetime
) -> list[ProactiveMessage]:
    """What the firing check-in sends: the prompt, then a "still relevant?" review
    prompt (with a Вирішено button) for each active concern due for review."""
    messages: list[ProactiveMessage] = [(build_prompt(), None)]
    for condition in await concerns.due_for_review(session, user_id=user_id, now=now):
        text = assert_safe_output(locale.CHECKIN_REVIEW_PROMPT.format(name=condition.name))
        buttons = [(locale.BTN_PROBLEM_RESOLVED, callbacks.problem_resolve(condition.id))]
        messages.append((text, buttons))
        await concerns.mark_reviewed(session, condition.id, now)
    return messages


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "."))


@dataclass(frozen=True)
class ParsedCheckIn:
    """Structured fields pulled from a free-text check-in answer (best-effort)."""

    sleep_hours: float | None = None
    water_ml: int | None = None
    mood: int | None = None
    training: str | None = None
    symptoms: frozenset[Symptom] = field(default_factory=frozenset)


def parse_checkin(text: str) -> ParsedCheckIn:
    """Parse a free-text check-in answer into structured fields (pure)."""
    lowered = text.casefold()

    sleep_hours = None
    if m := re.search(rf"{_NUM}\s*(?:год|h)", lowered):
        sleep_hours = _to_float(m.group(1))

    water_ml = None
    if m := re.search(rf"{_NUM}\s*(?:л(?:ітр\w*)?)\b", lowered):
        water_ml = int(_to_float(m.group(1)) * 1000)
    elif m := re.search(rf"{_NUM}\s*мл\b", lowered):
        water_ml = int(_to_float(m.group(1)))

    mood = None
    mood_match = re.search(rf"настр\w*\s*{_NUM}", lowered) or re.search(
        rf"{_NUM}\s*(?:/|з)\s*5", lowered
    )
    if mood_match:
        mood = max(1, min(5, int(_to_float(mood_match.group(1)))))

    training = "так" if any(hint in lowered for hint in _TRAINING_HINTS) else None

    return ParsedCheckIn(
        sleep_hours=sleep_hours,
        water_ml=water_ml,
        mood=mood,
        training=training,
        symptoms=detect_symptoms(text),
    )


@dataclass(frozen=True)
class CheckInResult:
    """The reply to a check-in: an acknowledgement, plus any triage escalation."""

    message: str
    escalated: bool


async def process_checkin(
    session: AsyncSession, *, user: User, text: str, check_date: date | None = None
) -> CheckInResult:
    """Persist a check-in and append deterministic guidance if the gate escalates.

    The reply runs through :func:`dbaylo.safety.gate.screen` — so a check-in that
    mentions a red-flag symptom (triage) *or* a disordered-eating signal (wellness
    guardrail) is surfaced, with triage winning when both appear. The escalation is
    produced entirely by the deterministic cores; the LLM is never consulted here.
    """
    parsed = parse_checkin(text)
    row = CheckIn(
        user_id=user.id,
        check_date=check_date or date.today(),
        sleep_hours=parsed.sleep_hours,
        water_ml=parsed.water_ml,
        mood=parsed.mood,
        training=parsed.training,
        symptoms=",".join(sorted(s.value for s in parsed.symptoms)) or None,
    )
    session.add(row)
    await session.flush()

    decision = screen(text)
    if decision.cleared:
        return CheckInResult(message=locale.CHECKIN_SAVED, escalated=False)

    message = assert_safe_output(f"{locale.CHECKIN_SAVED}\n\n{decision.message}")
    return CheckInResult(message=message, escalated=True)


async def has_checkin_on(session: AsyncSession, *, user_id: int, day: date) -> bool:
    """True iff a check-in already exists for the user on ``day``."""
    count = await session.scalar(
        select(func.count())
        .select_from(CheckIn)
        .where(CheckIn.user_id == user_id, CheckIn.check_date == day)
    )
    return bool(count)


async def should_send_nudge(session: AsyncSession, *, user_id: int, day: date) -> bool:
    """Send the single gentle nudge only when no check-in arrived that day."""
    return not await has_checkin_on(session, user_id=user_id, day=day)


def _dry_run() -> int:
    print(build_prompt())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.companion.checkin")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the check-in prompt; send nothing"
    )
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("only --dry-run is supported from the CLI")
    return _dry_run()


if __name__ == "__main__":
    sys.exit(main())
