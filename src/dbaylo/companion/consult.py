"""Contextual consultation — a grounded, multi-turn 'ask Дбайло about THIS result'.

When the user opens a specific subject (one indicator's trend, or a whole report's reading) and
taps "Запитати Дбайло", this answers their question FROM THE REAL DATA: the flow hands us a
deterministic, grounded context (built in :mod:`consult_context`), and the model is told to base
every statement on it — never to invent values, references, or trends.

Safety contract (identical in spirit to :mod:`companion.intake`):

* The **deterministic triage core still owns escalation.** Every turn, the accumulated user text
  is run through :func:`dbaylo.safety.screen`; a red flag (``URGENT_CARE`` / ``EMERGENCY``) or a
  disordered-eating signal is surfaced verbatim and **leads** the reply; the LLM cannot lower it.
* Every reply passes ``assert_safe_output`` (no dose, no restrictive-diet numbers, no
  "skip the doctor") and ends with the disclaimer; a deterministic fallback covers any LLM failure.

The only LLM use is the answer itself, always downstream of ``screen`` — so this module imports
``dbaylo.safety`` and never the escalation engines directly (the AST choke-point test enforces it).
"""

from __future__ import annotations

from dataclasses import dataclass

from dbaylo import locale
from dbaylo.labs.extraction import Runner
from dbaylo.labs.humanize import strip_markup
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
from dbaylo.safety import GateSource, screen
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output
from dbaylo.triage.types import Action

Turn = dict[str, str]  # {"role": "user" | "assistant", "text": ...}

# Keep only the recent exchange in the model's view — enough for a coherent consultation without
# the prompt growing unbounded across a long back-and-forth.
MAX_CONTEXT_TURNS = 8

CONSULT_PERSONA = (
    "You are Дбайло, an experienced and caring medical consultant talking the user through THEIR "
    "OWN lab result, one on one. You are given GROUNDED DATA about a single subject (one "
    "indicator's trend, or a whole report). Base EVERY statement on that data — never invent or "
    "assume values, reference ranges, or trends that are not in it; if something is not in the "
    "data, say so plainly and suggest how to find out.\n"
    "Reply EXCLUSIVELY in natural, warm Ukrainian, addressing the user as 'ти' — like a real, "
    "knowledgeable doctor-friend who genuinely wants to help them understand and solve their "
    "question. Explain in plain words what the result means FOR THIS CASE, what an out-of-range "
    "value MAY point to (cautiously — 'може свідчити про…', never a definite diagnosis), what is "
    "worth watching, and whether / how soon / which doctor to see. When it would genuinely help "
    "the consultation, ask ONE focused clarifying question — do not interrogate.\n"
    "A deterministic safety check runs alongside you and decides urgency: you are told its level "
    "and must NEVER go below it or imply the user can skip care. NEVER give: a definitive "
    "diagnosis; a "
    "medication, supplement, or any dose; calorie/macro/fasting numbers; fabricated studies, "
    "sources, or statistics. Do not use the phrases 'все добре', 'усе добре', 'ти здоровий', "
    "'ти здорова', 'не хвилюйся', 'нічого страшного' — describe the data instead. Be concrete and "
    "genuinely useful but careful: 2–5 short sentences, plain text, no markdown. Do NOT add your "
    "own 'я не лікар' disclaimer — it is appended automatically.\n" + NATURAL_VOICE
)


@dataclass(frozen=True)
class ConsultReply:
    """One consultation turn's reply (already safety-checked + disclaimer-appended)."""

    text: str
    source: str  # "triage" | "guardrail" | "llm" | "fallback"


def _accumulated_user_text(transcript: list[Turn]) -> str:
    return "\n".join(t["text"] for t in transcript if t.get("role") == "user")


def _safety_lead(decision_source: GateSource, decision: object) -> str | None:
    """The deterministic message that must LEAD the reply, if escalation is warranted — a red-flag
    triage (>= urgent care) or any disordered-eating guardrail signal. The LLM cannot lower it."""
    triage = getattr(decision, "triage", None)
    guardrail = getattr(decision, "guardrail", None)
    if decision_source is GateSource.TRIAGE and triage is not None:
        if triage.action >= Action.URGENT_CARE:
            return str(triage.message)
    elif decision_source is GateSource.GUARDRAIL and guardrail is not None:
        return str(guardrail.message)
    return None


def _prompt(context: str, transcript: list[Turn], *, triage_level: str) -> str:
    lines = [
        "GROUNDED DATA about the subject — answer ONLY from this; do not invent anything:",
        context,
        "",
        f"Deterministic triage level (do not go below this): {triage_level}.",
        "Conversation so far (answer the user's latest message):",
    ]
    for turn in transcript[-MAX_CONTEXT_TURNS:]:
        who = "Користувач" if turn.get("role") == "user" else "Дбайло"
        lines.append(f"{who}: {turn.get('text', '')}")
    return "\n".join(lines)


async def consult(
    context: str,
    transcript: list[Turn],
    *,
    runner: Runner = run_claude,
    model: str | None = None,
) -> ConsultReply:
    """Answer the user's latest question about a grounded subject, safely.

    Runs the deterministic triage backstop over the accumulated user text, then the guarded LLM
    answer grounded in ``context``; a high escalation always leads the reply and is never softened.
    """
    decision = screen(_accumulated_user_text(transcript))
    triage = decision.triage
    triage_level = triage.action.name if triage is not None else Action.MONITOR.name
    lead = _safety_lead(decision.source, decision)

    body = locale.CONSULT_FALLBACK
    source = "fallback"
    try:
        result = await runner(
            _prompt(context, transcript, triage_level=triage_level),
            append_system_prompt=CONSULT_PERSONA,
            model=model,
        )
    except ClaudeUnavailable:
        result = None
    if result is not None and result.ok and result.text.strip():
        try:
            body = assert_safe_output(strip_markup(result.text.strip()))
            source = "llm"
        except ValueError:
            body, source = locale.CONSULT_FALLBACK, "fallback"

    if lead is not None:
        source = decision.source.value
    combined = f"{lead}\n\n{body}" if lead else body
    return ConsultReply(text=f"{assert_safe_output(combined)}\n\n{DISCLAIMER}", source=source)
