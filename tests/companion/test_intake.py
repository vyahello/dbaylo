"""Stage 6B — conversational symptom intake: routing + the deterministic triage backstop.

The intake gives an LLM-led interview, but the deterministic triage core still owns
escalation: a red flag must LEAD the reply and is never softened; every reply is guarded
and disclaimer-appended; any LLM failure / guard trip falls back to a safe template.
"""

from __future__ import annotations

import pytest

from dbaylo import locale
from dbaylo.companion import intake
from dbaylo.llm import ClaudeResult, ClaudeUnavailable
from dbaylo.safety import screen
from dbaylo.triage.safety import DISCLAIMER, contains_dose_directive, contains_forbidden_reassurance


def _runner(text: str, ok: bool = True):
    async def run(*args, **kwargs) -> ClaudeResult:
        return ClaudeResult(ok=ok, text=text, raw_stdout=text, exit_code=0 if ok else 1)

    return run


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("болить голова вже два дні", True),
        ("сильно нудить і температура", True),
        ("кашель не проходить", True),
        ("запаморочення зранку", True),
        ("дякую, гарного дня", False),
        ("хочу поставити ціль більше рухатися", False),
        ("яка погода сьогодні", False),
    ],
)
def test_looks_like_complaint(text, expected) -> None:
    assert intake.looks_like_complaint(text) is expected


async def test_advance_uses_safe_model_text() -> None:
    body = "Розкажи, будь ласка, де саме болить і коли це почалося?"
    transcript = [{"role": "user", "text": "болить голова"}]
    reply = await intake.advance(transcript, runner=_runner(body))
    assert body in reply.text
    assert reply.text.endswith(DISCLAIMER)
    assert reply.done is False  # first turn -> keep interviewing


async def test_advance_falls_back_on_forbidden_phrase() -> None:
    transcript = [{"role": "user", "text": "болить голова"}]
    reply = await intake.advance(
        transcript, runner=_runner("Все добре, не хвилюйся, лікар не потрібен.")
    )
    assert "не хвилюйся" not in reply.text
    assert contains_forbidden_reassurance(reply.text) is None
    assert locale.INTAKE_FALLBACK in reply.text


async def test_advance_falls_back_when_claude_unavailable() -> None:
    async def boom(*args, **kwargs):
        raise ClaudeUnavailable("no binary")

    reply = await intake.advance([{"role": "user", "text": "болить горло"}], runner=boom)
    assert locale.INTAKE_FALLBACK in reply.text and reply.text.endswith(DISCLAIMER)


async def test_triage_backstop_leads_on_emergency() -> None:
    # An emergency red flag must lead the reply regardless of the model's chatter,
    # and the LLM can never lower it.
    transcript = [{"role": "user", "text": "не можу помочитися"}]
    reply = await intake.advance(transcript, runner=_runner("Розкажи більше деталей."))
    escalation = screen("не можу помочитися").triage
    assert escalation is not None
    assert escalation.message in reply.text  # the deterministic escalation leads


async def test_triage_backstop_leads_on_urgent() -> None:
    transcript = [{"role": "user", "text": "висока температура й озноб, не можу пити"}]
    reply = await intake.advance(transcript, runner=_runner("Деталі?"))
    escalation = screen("висока температура й озноб, не можу пити").triage
    assert escalation is not None and escalation.message in reply.text


async def test_advance_concludes_after_max_turns() -> None:
    transcript = [{"role": "user", "text": f"відповідь {i}"} for i in range(intake.MAX_TURNS)]
    reply = await intake.advance(transcript, runner=_runner("Підсумок: ..."))
    assert reply.done is True  # bounded — the FSM never lingers


def test_intake_fallback_and_persona_are_safe() -> None:
    assert contains_forbidden_reassurance(locale.INTAKE_FALLBACK) is None
    assert contains_dose_directive(locale.INTAKE_FALLBACK) is None
