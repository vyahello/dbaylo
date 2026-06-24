"""Contextual consultation engine (``companion.consult``) — grounded answer + safety backstop.

The consult gives an LLM answer grounded in a deterministic context, but the deterministic triage
core still owns escalation: a red flag LEADS the reply and is never softened; every reply is guarded
and disclaimer-appended; any LLM failure / guard trip falls back to a safe template. The model must
also be HANDED the grounded context (so it answers from real data, not invention).
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.companion import consult
from dbaylo.llm import ClaudeResult, ClaudeUnavailable
from dbaylo.safety import screen
from dbaylo.triage.safety import DISCLAIMER, contains_dose_directive, contains_forbidden_reassurance

_CONTEXT = (
    "Subject: a single lab indicator — 'Холестерин' (sample: blood).\n"
    "Measurements over time (date | value | reference | status):\n"
    "- 2023-01-01 | 6.2 ммоль/л | ≤ 5.2 | OUT OF RANGE\n"
    "Range-relative trend across these points: STABLE_OUT_OF_RANGE."
)


def _runner(text: str, ok: bool = True):
    captured: dict[str, str] = {}

    async def run(prompt: str, *args, **kwargs) -> ClaudeResult:
        captured["prompt"] = prompt
        return ClaudeResult(ok=ok, text=text, raw_stdout=text, exit_code=0 if ok else 1)

    run.captured = captured  # type: ignore[attr-defined]
    return run


async def test_consult_grounds_the_answer_and_appends_disclaimer() -> None:
    body = "Твій холестерин 6.2 вищий за норму ≤5.2 — варто обговорити це з лікарем."
    transcript = [{"role": "user", "text": "що означає мій холестерин?"}]
    runner = _runner(body)
    reply = await consult.consult(_CONTEXT, transcript, runner=runner)
    assert body in reply.text and reply.text.endswith(DISCLAIMER)
    assert reply.source == "llm"
    # The grounded context AND the user's question were handed to the model.
    assert "Холестерин" in runner.captured["prompt"]  # type: ignore[attr-defined]
    assert "що означає мій холестерин" in runner.captured["prompt"]  # type: ignore[attr-defined]


async def test_consult_falls_back_on_forbidden_phrase() -> None:
    transcript = [{"role": "user", "text": "це погано?"}]
    reply = await consult.consult(
        _CONTEXT, transcript, runner=_runner("Все добре, до лікаря йти не треба.")
    )
    assert "не треба" not in reply.text
    assert contains_forbidden_reassurance(reply.text) is None
    assert locale.CONSULT_FALLBACK in reply.text and reply.source == "fallback"


async def test_consult_falls_back_when_claude_unavailable() -> None:
    async def boom(*args, **kwargs):
        raise ClaudeUnavailable("no binary")

    reply = await consult.consult(_CONTEXT, [{"role": "user", "text": "?"}], runner=boom)
    assert locale.CONSULT_FALLBACK in reply.text and reply.text.endswith(DISCLAIMER)


async def test_consult_triage_backstop_leads_on_emergency() -> None:
    # A red flag in the user's question must LEAD the reply (deterministic), even mid-consult about
    # a lab value — the LLM can never lower it.
    transcript = [{"role": "user", "text": "а ще я не можу помочитися"}]
    reply = await consult.consult(
        _CONTEXT, transcript, runner=_runner("Поговорімо про холестерин.")
    )
    escalation = screen("а ще я не можу помочитися").triage
    assert escalation is not None
    assert escalation.message in reply.text  # the deterministic escalation leads
    assert reply.source == "triage"


async def test_consult_preserves_light_markup_for_premium_rendering() -> None:
    # The reply KEEPS the *bold*/_italic_ markers (the flow renders them to HTML) — it no longer
    # strips them to plain text.
    body = "Твій *холестерин 6.2* трохи вищий. _Не гостро, але варто перевірити._"
    reply = await consult.consult(_CONTEXT, [{"role": "user", "text": "?"}], runner=_runner(body))
    assert "*холестерин 6.2*" in reply.text and "_Не гостро" in reply.text


async def test_consult_strips_a_model_added_duplicate_disclaimer() -> None:
    # The model sometimes appends its OWN 'я не лікар' line; we always append the canonical
    # DISCLAIMER, so the duplicate is dropped — exactly one disclaimer remains.
    body = "Холестерин трохи вищий.\n\nЯ не лікар, і це не замінює консультацію з фахівцем."
    reply = await consult.consult(_CONTEXT, [{"role": "user", "text": "?"}], runner=_runner(body))
    assert "не замінює консультацію з фахівцем" not in reply.text  # the model's duplicate is gone
    assert reply.text.endswith(DISCLAIMER)  # the one canonical disclaimer remains


async def test_consult_rejects_a_superlative_clinic_recommendation() -> None:
    # The consult may discuss clinics now, so rail #4 applies: ranking a provider as "best" /
    # "operate here" must trip the guard -> deterministic fallback, never sent.
    body = "Найкраща клініка для цього — «Оберіг», оперуйся саме там."
    reply = await consult.consult(
        _CONTEXT, [{"role": "user", "text": "де зробити?"}], runner=_runner(body)
    )
    assert "Найкраща клініка" not in reply.text and reply.source == "fallback"


def test_consult_fallback_and_persona_are_safe() -> None:
    assert contains_forbidden_reassurance(locale.CONSULT_FALLBACK) is None
    assert contains_dose_directive(locale.CONSULT_FALLBACK) is None
