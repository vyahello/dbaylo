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


def _capturing_runner(text: str):
    captured: dict[str, object] = {}

    async def run(prompt: str, *args, **kwargs) -> ClaudeResult:
        captured["prompt"] = prompt
        return ClaudeResult(ok=True, text=text, raw_stdout=text, exit_code=0)

    run.captured = captured  # type: ignore[attr-defined]
    return run


async def test_companion_grounds_in_the_patient_context_when_given() -> None:
    # The grounding fix: a patient profile (problems + analyses) is handed to the model so the
    # general reply can be based on the user's real picture, not "пальцем у небо".
    runner = _capturing_runner("Памʼятаю про твої нирки — ось що варто.")
    ctx = "PATIENT PROFILE: - Health concerns: Камені в нирках."
    reply = await generate_reply("щось тягне поперек", context=ctx, runner=runner)
    assert reply.source == "llm"
    assert "Камені в нирках" in runner.captured["prompt"]  # type: ignore[attr-defined]
    assert "щось тягне поперек" in runner.captured["prompt"]  # type: ignore[attr-defined]


async def test_companion_stays_general_without_context() -> None:
    # No profile and no history -> the prompt is just the user's text (a plain single-turn reply).
    runner = _capturing_runner("Тримайся, друже.")
    await generate_reply("як справи?", context="", runner=runner)
    assert runner.captured["prompt"] == "як справи?"  # type: ignore[attr-defined]


async def test_companion_threads_on_prior_history() -> None:
    # The unified-chat fix: prior turns are handed to the model so a multi-message conversation
    # threads (it answers the LATEST line in context), instead of replying to each message cold.
    runner = _capturing_runner("А вже місяць — це варто перевірити.")
    history = [
        {"role": "user", "text": "я постійно втомлений"},
        {"role": "assistant", "text": "Давно це триває?"},
    ]
    reply = await generate_reply("вже десь місяць", context="", history=history, runner=runner)
    assert reply.source == "llm"
    prompt = runner.captured["prompt"]  # type: ignore[attr-defined]
    assert "я постійно втомлений" in prompt  # the earlier turn is in the prompt
    assert "Давно це триває?" in prompt  # Дбайло's own prior question too
    assert "вже десь місяць" in prompt  # and the latest message it must answer


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


def _model_capturing_runner():
    captured: dict[str, object] = {}

    async def run(prompt: str, *, append_system_prompt: str, model=None, **kw) -> ClaudeResult:
        captured["model"] = model
        return ClaudeResult(ok=True, text="ок", raw_stdout="ок", exit_code=0)

    run.captured = captured  # type: ignore[attr-defined]
    return run


async def test_chat_model_is_used_when_configured(monkeypatch) -> None:
    # Precision lever #5: CLAUDE_CHAT_MODEL lets the expert chat use a sharper model.
    from types import SimpleNamespace

    import dbaylo.companion.conversation as conv

    monkeypatch.setattr(conv, "get_settings", lambda: SimpleNamespace(claude_chat_model="opus"))
    runner = _model_capturing_runner()
    await generate_reply("як краще висипатися?", runner=runner)
    assert runner.captured["model"] == "opus"  # type: ignore[attr-defined]


async def test_chat_model_defaults_to_none_when_unset(monkeypatch) -> None:
    # Default (empty) keeps behaviour unchanged: model=None -> run_claude uses the default alias.
    from types import SimpleNamespace

    import dbaylo.companion.conversation as conv

    monkeypatch.setattr(conv, "get_settings", lambda: SimpleNamespace(claude_chat_model=""))
    runner = _model_capturing_runner()
    await generate_reply("привіт", runner=runner)
    assert runner.captured["model"] is None  # type: ignore[attr-defined]
