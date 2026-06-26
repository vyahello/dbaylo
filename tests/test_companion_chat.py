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
