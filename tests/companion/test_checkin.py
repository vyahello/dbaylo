"""Check-in: parsing, symptom -> triage routing, persistence, single no-nag nudge."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.companion import callbacks, checkin, concerns
from dbaylo.companion.checkin import (
    build_prompt,
    has_checkin_on,
    parse_checkin,
    process_checkin,
    should_send_nudge,
)
from dbaylo.db.models import CheckIn, User
from dbaylo.triage.types import Symptom


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=7, name="Test")
    session.add(user)
    await session.flush()
    return user


def test_build_prompt_is_safe_and_nonempty() -> None:
    assert build_prompt().strip()


async def test_grounded_prompt_uses_context_and_falls_back_when_empty() -> None:
    from dbaylo.llm import ClaudeResult

    captured: dict[str, object] = {}

    async def runner(prompt: str, *args, **kwargs) -> ClaudeResult:
        captured["prompt"] = prompt
        return ClaudeResult(
            ok=True, text="Привіт! Як нирки після КТ? І як спалося?", raw_stdout="", exit_code=0
        )

    ctx = "PATIENT PROFILE: - concerns: Камені в нирках.\nCURRENTLY out-of-range: - Глюкоза 7.0."
    out = await checkin.build_grounded_prompt(ctx, runner=runner)
    assert "Як нирки" in out  # the grounded opener
    assert "Камені в нирках" in captured["prompt"]  # the model was handed the health context

    # No context -> the static gentle prompt, and the LLM is NOT called.
    async def exploding(*args, **kwargs):
        raise AssertionError("no LLM when there's nothing to ground in")

    assert await checkin.build_grounded_prompt("", runner=exploding) == build_prompt()


async def test_process_checkin_remembers_the_users_words(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    await process_checkin(async_session, user=user, text="спав 5 годин, настрій 2/5, виснажений")
    rows = await checkin.recent_checkins(async_session, user_id=user.id)
    assert (
        rows and rows[0].note == "спав 5 годин, настрій 2/5, виснажений"
    )  # the raw words are kept


async def test_state_memory_context_summarises_recent_checkins(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    assert await checkin.state_memory_context(async_session, user_id=user.id) == ""  # nothing yet
    await process_checkin(async_session, user=user, text="спав 5 год, настрій 2/5, погано сплю")
    ctx = await checkin.state_memory_context(async_session, user_id=user.id)
    assert "RECENT CHECK-IN HISTORY" in ctx
    assert "сон 5 год" in ctx and "настрій 2/5" in ctx and "погано сплю" in ctx


async def test_grounded_context_combines_labs_and_state(async_session: AsyncSession) -> None:
    # The combined grounding feeds BOTH the proactive check-in and general chat.
    user = await _user(async_session)
    await concerns.add_active(async_session, user=user, name="Камені в нирках")
    await process_checkin(async_session, user=user, text="настрій 2/5, втомлений")
    ctx = await checkin.grounded_context(async_session, user_id=user.id, today=date(2026, 6, 25))
    assert "Камені в нирках" in ctx  # the labs/profile side
    assert "RECENT CHECK-IN HISTORY" in ctx and "втомлений" in ctx  # the state-memory side


async def test_grounded_prompt_falls_back_on_unsafe_output() -> None:
    from dbaylo.llm import ClaudeResult

    async def runner(*args, **kwargs) -> ClaudeResult:
        return ClaudeResult(ok=True, text="Все добре, не хвилюйся!", raw_stdout="", exit_code=0)

    # A forbidden reassurance trips the guard -> the safe static prompt.
    assert await checkin.build_grounded_prompt("CURRENTLY out-of-range: x", runner=runner) == (
        build_prompt()
    )


async def test_checkin_messages_appends_due_concern_review(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    condition = await concerns.add_active(async_session, user=user, name="високий тиск")
    t0 = datetime(2026, 1, 1)
    await concerns.mark_reviewed(async_session, condition.id, t0)

    # Not yet due: only the prompt (no buttons).
    fresh = await checkin.checkin_messages(async_session, user_id=user.id, now=t0)
    assert len(fresh) == 1 and fresh[0][1] is None

    # Due after a week: prompt + a "still relevant?" review with a named Вирішено button.
    due = await checkin.checkin_messages(async_session, user_id=user.id, now=t0 + timedelta(days=8))
    assert len(due) == 2
    _, buttons = due[1]
    assert buttons is not None
    assert "високий тиск" in buttons[0][0]  # the concern name is on the button
    assert buttons[0][1] == callbacks.problem_resolve(condition.id)

    # Sending it marks the concern reviewed, so it isn't asked again immediately.
    again = await checkin.checkin_messages(
        async_session, user_id=user.id, now=t0 + timedelta(days=8, minutes=1)
    )
    assert len(again) == 1


async def test_checkin_batches_multiple_due_concerns_into_one_message(
    async_session: AsyncSession,
) -> None:
    user = await _user(async_session)
    t0 = datetime(2026, 1, 1)
    c1 = await concerns.add_active(async_session, user=user, name="високий тиск")
    c2 = await concerns.add_active(async_session, user=user, name="біль у спині")
    await concerns.mark_reviewed(async_session, c1.id, t0)
    await concerns.mark_reviewed(async_session, c2.id, t0)

    due = await checkin.checkin_messages(async_session, user_id=user.id, now=t0 + timedelta(days=8))
    assert len(due) == 2  # the prompt + ONE batched review message (not one per concern)
    _, buttons = due[1]
    assert buttons is not None and len(buttons) == 2  # both concerns, one button each
    assert {b[1] for b in buttons} == {
        callbacks.problem_resolve(c1.id),
        callbacks.problem_resolve(c2.id),
    }


def test_parse_extracts_fields() -> None:
    parsed = parse_checkin("спав 7 годин, випив 2 л води, настрій 4, трохи побігав")
    assert parsed.sleep_hours == 7
    assert parsed.water_ml == 2000
    assert parsed.mood == 4
    assert parsed.training == "так"
    assert parsed.symptoms == frozenset()


def test_parse_millilitres_and_no_training() -> None:
    parsed = parse_checkin("спав 6 годин, 1500 мл води, настрій 3")
    assert parsed.water_ml == 1500
    assert parsed.training is None


def test_parse_picks_up_symptoms() -> None:
    parsed = parse_checkin("погано спав, температура і озноб")
    assert Symptom.FEVER in parsed.symptoms and Symptom.CHILLS in parsed.symptoms


async def test_process_persists_checkin(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    result = await process_checkin(async_session, user=user, text="спав 8 годин, настрій 5")
    assert not result.escalated
    count = await async_session.scalar(select(func.count()).select_from(CheckIn))
    assert count == 1


async def test_process_routes_symptoms_to_triage(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    result = await process_checkin(
        async_session, user=user, text="кепсько: температура, озноб і біль у боці"
    )
    assert result.escalated
    # The triage message is deterministic; it always escalates toward care.
    assert "екстрену" in result.message or "швидку" in result.message
    row = await async_session.scalar(select(CheckIn))
    assert row is not None and row.symptoms is not None
    assert "fever" in row.symptoms


async def test_disordered_checkin_now_escalates(async_session: AsyncSession) -> None:
    """Behavior change (Stage 3.5): a check-in now also runs the guardrail leg.

    Previously the check-in had only a triage leg; routing it through the gate
    closes the rail #6 gap (disordered-pattern signals in check-in text). No
    regression — there was no guardrail outcome here to change.
    """
    user = await _user(async_session)
    result = await process_checkin(async_session, user=user, text="я нічого не їм цілими днями")
    assert result.escalated
    assert locale.CHECKIN_SAVED in result.message
    # The wellness SUPPORT message, not a triage one.
    assert "фахівц" in result.message


async def test_symptom_outranks_disordered_in_checkin(async_session: AsyncSession) -> None:
    """Precedence: when both appear, triage (the medical red flag) wins."""
    user = await _user(async_session)
    result = await process_checkin(
        async_session, user=user, text="температура й озноб, і нічого не їм"
    )
    assert result.escalated
    # Triage guidance is surfaced; the guardrail leg never ran.
    assert "медичну" in result.message or "швидку" in result.message
    assert "фахівц" not in result.message


async def test_single_no_nag_logic(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    today = date(2026, 6, 19)
    # No check-in yet -> a nudge is due.
    assert await should_send_nudge(async_session, user_id=user.id, day=today)
    await process_checkin(async_session, user=user, text="спав 7 годин", check_date=today)
    # Once a check-in exists, the nudge is suppressed (never nags).
    assert await has_checkin_on(async_session, user_id=user.id, day=today)
    assert not await should_send_nudge(async_session, user_id=user.id, day=today)
