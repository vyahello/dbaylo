"""Conversational symptom intake — history-taking with a deterministic safety backstop.

When the user reports a complaint, Дбайло runs a short guided interview (where / when /
how severe / associated symptoms …) and then gives an expert-level assessment + practical
guidance — like a doctor's first conversation, but never a definitive diagnosis.

Safety contract (the reason this is trustworthy, not just a chatbot):

* The **deterministic triage core still owns escalation.** Every turn, the accumulated
  user text is run through :func:`dbaylo.safety.screen`; if it hits a red flag
  (``URGENT_CARE`` / ``EMERGENCY``) — or a disordered-eating signal — that escalation is
  surfaced verbatim and **leads** the reply. The LLM can never lower it.
* Every reply passes ``assert_safe_output`` (no dose, no restrictive-diet numbers, no
  "skip the doctor") and ends with the disclaimer; a deterministic fallback covers any
  LLM failure or guard trip.

The only LLM use here is the interview itself, and it is always downstream of ``screen``
(the gate) — so this module imports ``dbaylo.safety`` and never the escalation engines
directly (the AST choke-point test enforces that).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dbaylo import locale
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
from dbaylo.safety import GateSource, screen
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output
from dbaylo.triage.types import Action

# A short exchange: ask focused questions, then give the assessment. Bounded so the FSM
# never lingers (and a restart / command just ends it).
MAX_TURNS = 4

Turn = dict[str, str]  # {"role": "user" | "assistant", "text": ...}

INTAKE_PERSONA = (
    "You are Дбайло conducting a careful symptom intake (history-taking), like a doctor's "
    "first conversation. You are NOT a doctor and never give a definitive diagnosis. Reply in "
    "natural, correct Ukrainian. The user has a health complaint.\n"
    "Across a SHORT exchange: ask FOCUSED clarifying questions — where exactly, character of the "
    "symptom, when it started and how long, severity, what makes it better or worse, associated "
    "symptoms, relevant history/medication. Ask only a small batch (2–4 questions) per message, "
    "not an overwhelming list. When you have enough — or when told few exchanges remain — give "
    "your assessment: what the picture MAY suggest (cautiously, 'може бути пов'язано з…', never a "
    "definite diagnosis), practical self-care, and clearly WHEN to see a doctor and which kind.\n"
    "A deterministic safety check runs alongside you and decides urgency: you are told its level "
    "and must NEVER go below it or imply the user can skip care. NEVER give a definitive "
    "diagnosis, a medication or any dose, calorie/macro/fasting numbers, or fabricated sources. "
    "Do not use the phrases 'все добре', 'усе добре', 'ти здоровий', 'ти здорова', 'не хвилюйся', "
    "'нічого страшного'. Plain text only, no markdown.\n" + NATURAL_VOICE
)


@dataclass(frozen=True)
class IntakeReply:
    """One intake turn's reply, and whether the intake is finished."""

    text: str
    done: bool


# Physical-complaint stems with word boundaries (Unicode-aware). The "біль" alternative
# uses a lookahead so it does NOT fire on "більше / більший" (more/bigger).
_COMPLAINT_RE = re.compile(
    r"\b(?:бол(?:ить|ять|ю|іло|яч)|біль(?!ш)|ниє|нудит|нудот|блюва|температур|гарячк|"
    r"лихоман|запаморо|паморо|кашл|кашель|нежит|висип|свербіж|свербить|набряк|печія|"
    r"задишк|задих|серцебитт|слабкіст|пронос|діаре|закреп|оніміння|поколюван|запален)",
    re.IGNORECASE,
)


def looks_like_complaint(text: str) -> bool:
    """A broad (router-level) check that the text is a physical health complaint.

    Deliberately wider than the triage red-flag vocabulary (which stays the escalation
    authority): it decides only whether to *start* the interview. The interview itself is
    always gated by ``screen``.
    """
    return bool(_COMPLAINT_RE.search(text))


def _accumulated_user_text(transcript: list[Turn]) -> str:
    return "\n".join(t["text"] for t in transcript if t.get("role") == "user")


def _prompt(transcript: list[Turn], *, triage_level: str, exchanges_left: int) -> str:
    lines = [
        f"Deterministic triage level (do not go below this): {triage_level}.",
        f"Exchanges left before you must give your assessment: {exchanges_left}.",
        "Conversation so far:",
    ]
    for turn in transcript:
        who = "Користувач" if turn.get("role") == "user" else "Дбайло"
        lines.append(f"{who}: {turn.get('text', '')}")
    return "\n".join(lines)


def _safety_lead(decision_source: GateSource, decision: object) -> str | None:
    """The deterministic message that must LEAD the reply, if escalation is warranted."""
    triage = getattr(decision, "triage", None)
    guardrail = getattr(decision, "guardrail", None)
    if decision_source is GateSource.TRIAGE and triage is not None:
        if triage.action >= Action.URGENT_CARE:
            return str(triage.message)
    elif decision_source is GateSource.GUARDRAIL and guardrail is not None:
        return str(guardrail.message)
    return None


async def advance(
    transcript: list[Turn],
    *,
    runner: object = run_claude,
    model: str | None = None,
) -> IntakeReply:
    """Produce the next intake message for a transcript ending in the user's latest turn.

    Runs the deterministic triage backstop, then the (guarded) LLM interview; a high
    escalation always leads the reply and is never softened.
    """
    user_turns = sum(1 for t in transcript if t.get("role") == "user")
    done = user_turns >= MAX_TURNS

    decision = screen(_accumulated_user_text(transcript))
    triage = decision.triage
    triage_level = triage.action.name if triage is not None else Action.MONITOR.name
    lead = _safety_lead(decision.source, decision)

    body = locale.INTAKE_FALLBACK
    try:
        result = await runner(  # type: ignore[operator]
            _prompt(
                transcript,
                triage_level=triage_level,
                exchanges_left=0 if done else MAX_TURNS - user_turns,
            ),
            append_system_prompt=INTAKE_PERSONA,
            model=model,
        )
    except ClaudeUnavailable:
        result = None
    if result is not None and result.ok and result.text.strip():
        try:
            body = assert_safe_output(result.text.strip())
        except ValueError:
            body = locale.INTAKE_FALLBACK

    combined = f"{lead}\n\n{body}" if lead else body
    return IntakeReply(text=f"{assert_safe_output(combined)}\n\n{DISCLAIMER}", done=done)
