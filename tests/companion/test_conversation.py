"""Companion conversation: the safety cores run before the LLM, with a fallback."""

from __future__ import annotations

from dbaylo.companion.conversation import generate_reply
from dbaylo.llm import ClaudeResult, ClaudeUnavailable
from dbaylo.triage.safety import DISCLAIMER


def _runner(text: str, ok: bool = True):
    async def run(*args, **kwargs) -> ClaudeResult:
        return ClaudeResult(ok=ok, text=text, raw_stdout=text, exit_code=0 if ok else 1)

    return run


def _exploding_runner():
    async def run(*args, **kwargs) -> ClaudeResult:
        raise AssertionError("the LLM must not be called on this path")

    return run


async def test_symptoms_short_circuit_to_triage() -> None:
    reply = await generate_reply(
        "у мене температура, озноб і біль у боці", runner=_exploding_runner()
    )
    assert reply.source == "triage"
    assert DISCLAIMER in reply.text


async def test_disordered_text_short_circuits_to_guardrail() -> None:
    reply = await generate_reply("я нічого не їм цілими днями", runner=_exploding_runner())
    assert reply.source == "guardrail"
    assert DISCLAIMER in reply.text


async def test_safe_llm_reply_passes_through_with_disclaimer() -> None:
    body = "Чудово, що дбаєш про сон! Спробуй лягати в один час — це справді допомагає."
    reply = await generate_reply("як мені краще висипатися?", runner=_runner(body))
    assert reply.source == "llm"
    assert body in reply.text
    assert reply.text.endswith(DISCLAIMER)


async def test_unsafe_llm_reply_falls_back() -> None:
    # The model tries to emit a dose directive -> guard trips -> deterministic fallback.
    reply = await generate_reply(
        "що випити від голови?", runner=_runner("Приймай 2 таблетки парацетамолу.")
    )
    assert reply.source == "fallback"
    assert reply.text.endswith(DISCLAIMER)


async def test_llm_failure_falls_back() -> None:
    reply = await generate_reply("привіт", runner=_runner("", ok=False))
    assert reply.source == "fallback"


async def test_llm_unavailable_falls_back() -> None:
    async def unavailable(*args, **kwargs) -> ClaudeResult:
        raise ClaudeUnavailable("no binary")

    reply = await generate_reply("привіт", runner=unavailable)
    assert reply.source == "fallback"
