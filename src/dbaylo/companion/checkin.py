"""Daily check-in: prompt, parse, route symptoms to triage, persist.

The evening prompt is sent by the scheduler. The user's free-text answer is parsed
leniently (sleep / water / mood / training) and any symptom mention is routed to
the **deterministic** triage engine — the LLM never makes that escalation call.

"One gentle follow-up, never nag": ~90 min after the prompt the scheduler sends a
single follow-up whose TEXT depends on :func:`has_checkin_on` — a soft "I'm here"
when no check-in arrived yet, or a light "anything change since the morning?" when
the user already checked in (so the second daily touch never reads as guilt-tripping).
No streak/guilt language anywhere.

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
from dbaylo.companion import callbacks, concerns, health
from dbaylo.companion.symptoms import detect_symptoms
from dbaylo.db.models import CheckIn, User
from dbaylo.labs.extraction import Runner
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
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
    """The gentle generic evening check-in prompt (safety-checked) — used when there's nothing to
    ground in, or the LLM is unavailable."""
    return assert_safe_output(locale.CHECKIN_PROMPT)


# A PROACTIVE check-in: Дбайло messages first, grounded in the user's real picture (current concerns
# + out-of-range indicators) so it asks about what actually matters, like a caring assistant.
CHECKIN_PERSONA = (
    "You are Дбайло starting a gentle DAILY CHECK-IN — you message the user FIRST, like a caring "
    "personal health assistant who knows them. You are given their health context (tracked "
    "concerns + currently out-of-range indicators + resolved ones + the GOALS they are working "
    "toward) AND their RECENT CHECK-IN HISTORY (how they've been — sleep / mood / symptoms / their "
    "own words). Use the history to "
    "remember and follow up ('учора писав, що погано спав — як сьогодні?'), and notice a dynamic "
    "('настрій кілька днів просідає'). Sometimes ask how an active GOAL is going and cheer real "
    "progress (never with numeric targets). If the context has a 'TODAY'S FOCUS' line, LEAD this "
    "check-in by asking specifically, by name, about THAT tracked concern (and if it says the data "
    "is old, warmly suggest re-testing) — that is the user feeling their tracking is alive. "
    "Otherwise open warmly and SPECIFICALLY: lead with what is "
    "relevant to them now (their main current concern, a goal in progress, OR a recent state worth "
    "following up), "
    "reference it briefly, then a light general question (sleep / mood / how the body feels). "
    "Two things to weave in WHEN the data shows them (gently, not every day, never alarm): if a "
    "flagged indicator's latest measurement is several months OLD, suggest it's time to re-test; "
    "if an EARLY WARNING trend is listed (still in range but heading toward a limit), mention it "
    "softly as something to keep an eye on. GROUND only in the data given — never invent a value, "
    "cause or diagnosis. Keep "
    "it SHORT (2–4 warm sentences), plain Ukrainian, address the user as 'ти'. A fitting emoji is "
    "fine, and you MAY emphasise the ONE key term with light *bold* (single asterisks — the "
    "tracked indicator's name, or a 're-test' suggestion), at most once or twice, not a wall. "
    "NEVER give a medication or dose, calorie/fasting numbers, or tell them "
    "to skip a doctor, and never say 'все добре'. End with an open question so they reply.\n"
    + NATURAL_VOICE
)


async def build_grounded_prompt(
    context: str, *, runner: Runner = run_claude, model: str | None = None
) -> str:
    """A warm check-in opener grounded in the user's health context, or the gentle generic prompt
    when there is no context / the LLM is unavailable / the output trips the guard."""
    if not context.strip():
        return build_prompt()
    prompt = (
        f"Контекст здоровʼя користувача:\n{context}\n\nПочни щоденний чек-ін одним повідомленням."
    )
    try:
        result = await runner(prompt, append_system_prompt=CHECKIN_PERSONA, model=model)
    except ClaudeUnavailable:
        return build_prompt()
    if result is None or not result.ok or not result.text.strip():
        return build_prompt()
    try:
        return assert_safe_output(result.text.strip())
    except ValueError:
        return build_prompt()


# --- State memory across check-ins ----------------------------------------------------------------
# Дбайло remembers the user's recent self-reported state (sleep / mood / symptoms + their own words)
# so it can notice the DYNAMIC ("третій день поспіль погано спиш") and reference it next time — not
# start each check-in / chat from scratch. Deterministic: it just summarises stored CheckIn rows.

_RECENT_CHECKINS = 5


async def recent_checkins(
    session: AsyncSession, *, user_id: int, limit: int = _RECENT_CHECKINS
) -> list[CheckIn]:
    """The user's most recent check-ins (newest first)."""
    rows = await session.scalars(
        select(CheckIn).where(CheckIn.user_id == user_id).order_by(CheckIn.id.desc()).limit(limit)
    )
    return list(rows.all())


def _checkin_line(row: CheckIn) -> str:
    bits: list[str] = []
    if row.sleep_hours is not None:
        bits.append(f"сон {row.sleep_hours:g} год")
    if row.mood is not None:
        bits.append(f"настрій {row.mood}/5")
    if row.water_ml is not None:
        bits.append(f"вода {row.water_ml} мл")
    if row.training:
        bits.append("була активність")
    if row.symptoms:
        bits.append(f"симптоми: {row.symptoms}")
    note = (row.note or "").strip()
    if note:
        bits.append(f"його слова: «{note[:140]}»")
    day = row.check_date.isoformat() if row.check_date else "?"
    return f"- {day}: " + ("; ".join(bits) if bits else "(без деталей)")


async def state_memory_context(session: AsyncSession, *, user_id: int) -> str:
    """A grounded block of the user's recent check-in STATE (sleep / mood / symptoms / their words),
    so Дбайло notices the dynamic and references it. ``""`` when there are no check-ins yet."""
    rows = await recent_checkins(session, user_id=user_id)
    if not rows:
        return ""
    header = (
        "RECENT CHECK-IN HISTORY (the user's self-reported state across recent check-ins — use it "
        "to NOTICE the dynamic and reference how they've been, e.g. 'третій день поспіль скаржишся "
        "на сон'; most recent first):"
    )
    return "\n".join([header, *(_checkin_line(row) for row in rows)])


async def grounded_context(session: AsyncSession, *, user_id: int, today: date) -> str:
    """The full grounded context: the lab health picture (``health``) + the recent check-in STATE
    memory. Shared by the proactive check-in and the general companion chat / symptom intake, so
    both answer/ask from real data AND remember how they've been. ``""`` when there's nothing."""
    labs = await health.build_health_context(session, user_id, today=today)
    state = await state_memory_context(session, user_id=user_id)
    return "\n\n".join(part for part in (labs, state) if part)


async def full_checkin_context(session: AsyncSession, *, user_id: int, today: date) -> str:
    """The check-in's COMPLETE grounding: the lab/profile picture + state memory
    (``grounded_context``) PLUS the rotating tracked-concern FOCUS (one concern per day + a re-test
    nudge when its data is stale). Used by BOTH the scheduled check-in AND the manual 📝 button, so
    the two are built identically — the manual one is not a poorer version of the scheduled one."""
    base = await grounded_context(session, user_id=user_id, today=today)
    focus = await health.checkin_focus_block(session, user_id, today=today)
    return "\n\n".join(part for part in (base, focus) if part)


async def checkin_messages(
    session: AsyncSession, *, user_id: int, now: datetime, runner: Runner = run_claude
) -> list[ProactiveMessage]:
    """What the firing check-in sends: a GROUNDED prompt (asks about the user's actual concerns +
    data + recent state), then — if any concerns are due for review — ONE batched "still relevant?"
    message with a "✅ <name>" button per concern (not a separate message each)."""
    # Full grounding incl. the rotating tracked-concern focus — shared with the manual button.
    full_context = await full_checkin_context(session, user_id=user_id, today=now.date())
    messages: list[ProactiveMessage] = [
        (await build_grounded_prompt(full_context, runner=runner), None)
    ]
    due = await concerns.due_for_review(session, user_id=user_id, now=now)
    if due:
        buttons = [
            (
                locale.BTN_PROBLEM_RESOLVED_NAMED.format(name=condition.name[:32]),
                callbacks.problem_resolve(condition.id),
            )
            for condition in due
        ]
        messages.append((assert_safe_output(locale.CHECKIN_REVIEW_HEADER), buttons))
        for condition in due:
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
        note=text.strip()[:500] or None,  # the user's own words -> state memory across check-ins
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
