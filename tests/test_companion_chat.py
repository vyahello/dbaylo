"""Unified expert chat thread: general free-text chat THREADS and remembers.

The companion's free-text path is no longer a stateless one-shot. ``_run_companion_turn`` keeps the
recent back-and-forth in FSM data (so a multi-message conversation threads), recalls the grounded
context + cross-session memory, and saves the SUBSTANTIVE exchange to durable memory — the same
store the consult recalls from. These tests pin that behaviour without touching the DB or the LLM.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dbaylo.bot import companion_flow
from dbaylo.companion.conversation import CompanionReply


@asynccontextmanager
async def _noop_typing(message):
    yield  # the 'typing…' indicator is irrelevant to the threading logic


def _message():
    message = AsyncMock()
    message.text = "..."
    message.from_user = SimpleNamespace(id=4242, full_name="Owner")
    message.chat = SimpleNamespace(id=4242)
    return message


def _state(data: dict):
    state = AsyncMock()
    state.get_data = AsyncMock(return_value=data)
    return state


def _patch_common(monkeypatch, *, reply_source: str = "llm"):
    """Stub the slow/IO collaborators; return the captured generate_reply + _remember_general."""
    monkeypatch.setattr(companion_flow, "keep_typing", _noop_typing)
    monkeypatch.setattr(companion_flow, "_grounded_context", AsyncMock(return_value="CTX"))
    generate = AsyncMock(return_value=CompanionReply(text="Ось що варто.", source=reply_source))
    monkeypatch.setattr(companion_flow, "generate_reply", generate)
    remember = AsyncMock()
    monkeypatch.setattr(companion_flow, "_remember_general", remember)
    return generate, remember


async def test_companion_turn_threads_and_remembers_substantive(monkeypatch) -> None:
    generate, remember = _patch_common(monkeypatch)
    message, state = _message(), _state({})  # a brand-new thread

    await companion_flow._run_companion_turn(
        state=state, message=message, text="що з моїм залізом?"
    )

    # First turn: no prior history is handed to the model.
    assert generate.await_args.kwargs["history"] == []
    # The exchange is threaded back into FSM data (user + assistant), with a freshness timestamp.
    saved = state.update_data.await_args.kwargs
    transcript = saved["chat_transcript"]
    assert [t["role"] for t in transcript] == ["user", "assistant"]
    assert transcript[0]["text"] == "що з моїм залізом?"
    assert "chat_ts" in saved
    # A substantive turn is persisted to durable cross-session memory.
    remember.assert_awaited_once()
    assert remember.await_args.args[1] == "що з моїм залізом?"


async def test_companion_turn_continues_a_fresh_thread(monkeypatch) -> None:
    generate, _ = _patch_common(monkeypatch)
    prior = [
        {"role": "user", "text": "я постійно втомлений"},
        {"role": "assistant", "text": "Давно це триває?"},
    ]
    ts = companion_flow._now().isoformat()  # recent -> the thread is still alive
    message = _message()
    state = _state({"chat_transcript": prior, "chat_ts": ts})

    await companion_flow._run_companion_turn(state=state, message=message, text="вже місяць")

    assert generate.await_args.kwargs["history"] == prior  # the model sees the running conversation
    transcript = state.update_data.await_args.kwargs["chat_transcript"]
    assert transcript[-2]["text"] == "вже місяць"  # the new exchange is appended


async def test_companion_turn_resets_a_stale_thread(monkeypatch) -> None:
    generate, _ = _patch_common(monkeypatch)
    stale = {
        "chat_transcript": [{"role": "user", "text": "вчорашня розмова"}],
        "chat_ts": "2020-01-01T00:00:00+02:00",  # older than the TTL -> a fresh thread starts
    }
    await companion_flow._run_companion_turn(
        state=_state(stale), message=_message(), text="привіт знову"
    )
    assert generate.await_args.kwargs["history"] == []  # the stale history is dropped


async def test_companion_turn_skips_memory_for_a_bare_greeting(monkeypatch) -> None:
    _, remember = _patch_common(monkeypatch)
    await companion_flow._run_companion_turn(state=_state({}), message=_message(), text="дякую")
    remember.assert_not_awaited()  # a bare ack is threaded for the moment but not stored forever


async def test_companion_turn_skips_memory_on_fallback(monkeypatch) -> None:
    # A guard-tripped / unavailable LLM reply (source != "llm") is never written to memory.
    _, remember = _patch_common(monkeypatch, reply_source="fallback")
    await companion_flow._run_companion_turn(
        state=_state({}), message=_message(), text="розкажи про мої аналізи детально"
    )
    remember.assert_not_awaited()


async def test_substantive_turn_carries_the_affordance_keyboard(monkeypatch) -> None:
    from dbaylo.companion import callbacks

    _patch_common(monkeypatch)
    message, state = _message(), _state({})
    await companion_flow._run_companion_turn(
        state=state, message=message, text="що з моїм залізом?"
    )
    kb = message.answer.call_args.kwargs.get("reply_markup")
    assert kb is not None  # #6: a substantive turn offers 🔔 / 🏥
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert callbacks.CHAT_REMIND in datas and callbacks.CHAT_CLINICS in datas


async def test_trivial_turn_has_no_affordance_keyboard(monkeypatch) -> None:
    _patch_common(monkeypatch)
    message, state = _message(), _state({})
    await companion_flow._run_companion_turn(state=state, message=message, text="дякую")
    assert message.answer.call_args.kwargs.get("reply_markup") is None  # no buttons on a bare ack


def _patch_routers_off(monkeypatch):
    """Turn off the consult sub-routers so _engage_with_text falls to the price/chat step."""
    for name in ("start_data_question_consult", "start_primed_consult", "start_typed_affordance"):
        monkeypatch.setattr(companion_flow.consult_flow, name, AsyncMock(return_value=False))


async def test_free_form_price_request_routes_to_the_price_agent(monkeypatch) -> None:
    # "знайди ціни на парацетамол" is ACTED on (price agent), not just chatted about.
    _patch_routers_off(monkeypatch)
    sent = AsyncMock()
    monkeypatch.setattr(companion_flow.navigator_flow, "send_freeform_price", sent)
    message, state = _message(), _state({})
    await companion_flow._engage_with_text(
        message, state, "знайди ціни на парацетамол", AsyncMock()
    )
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "знайди ціни на парацетамол"  # the request text is passed


async def test_ordinary_chat_does_not_route_to_the_price_agent(monkeypatch) -> None:
    _patch_routers_off(monkeypatch)
    _patch_common(monkeypatch)
    sent = AsyncMock()
    monkeypatch.setattr(companion_flow.navigator_flow, "send_freeform_price", sent)
    message, state = _message(), _state({})
    await companion_flow._engage_with_text(message, state, "розкажи про сон", AsyncMock())
    sent.assert_not_awaited()  # a non-price turn stays in the companion chat


def test_worth_remembering_filters_trivial_turns() -> None:
    assert companion_flow._worth_remembering("чому в мене низький гемоглобін?")
    assert not companion_flow._worth_remembering("  Дякую  ")
    assert not companion_flow._worth_remembering("ок")
    assert not companion_flow._worth_remembering("")


def test_thread_fresh_rejects_old_or_malformed_timestamps() -> None:
    assert companion_flow._thread_fresh(companion_flow._now().isoformat())
    assert not companion_flow._thread_fresh("2020-01-01T00:00:00+02:00")
    assert not companion_flow._thread_fresh(None)
    assert not companion_flow._thread_fresh("not-a-date")


# --- The check-in answer CONTINUES the conversation (no dead-end "Занотував") ------


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()


def _patch_checkin_engage(monkeypatch):
    """Stub the check-in answer's collaborators; return the (intake, companion) turn spies."""
    monkeypatch.setattr(companion_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        companion_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=1))
    )
    monkeypatch.setattr(companion_flow.checkin, "process_checkin", AsyncMock())
    monkeypatch.setattr(companion_flow, "_grounded_context", AsyncMock(return_value="CTX"))
    intake_turn = AsyncMock()
    companion = AsyncMock()
    monkeypatch.setattr(companion_flow, "_run_intake_turn", intake_turn)
    monkeypatch.setattr(companion_flow, "_run_companion_turn", companion)
    for name in ("start_data_question_consult", "start_primed_consult", "start_typed_affordance"):
        monkeypatch.setattr(companion_flow.consult_flow, name, AsyncMock(return_value=False))
    return intake_turn, companion


async def test_checkin_answer_with_a_complaint_continues_into_the_interview(monkeypatch) -> None:
    # The owner's bug: answering the check-in with a symptom dead-ended at "Занотував". Now it LOGS
    # the state (process_checkin) AND flows into the history-taking interview.
    intake_turn, companion = _patch_checkin_engage(monkeypatch)
    message = _message()
    message.text = "тисне таз і важкість у попереку — тиждень тому вийшов камінь з нирки"
    await companion_flow.on_checkin_answer(message, _state({}), AsyncMock())
    companion_flow.checkin.process_checkin.assert_awaited_once()  # the state was still logged
    intake_turn.assert_awaited_once()  # ...and the conversation CONTINUED into the interview
    companion.assert_not_awaited()


async def test_checkin_answer_benign_continues_into_companion_chat(monkeypatch) -> None:
    # A benign check-in answer still ENGAGES — a grounded companion reply, not a one-shot ack.
    intake_turn, companion = _patch_checkin_engage(monkeypatch)
    message = _message()
    message.text = "спав 7 годин, настрій нормальний"
    await companion_flow.on_checkin_answer(message, _state({}), AsyncMock())
    intake_turn.assert_not_awaited()
    companion.assert_awaited_once()
