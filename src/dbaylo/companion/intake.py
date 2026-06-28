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

from dbaylo import locale, persona
from dbaylo.config import get_settings
from dbaylo.labs.humanize import strip_self_disclaimer
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
from dbaylo.safety import GateSource, screen
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output
from dbaylo.triage.types import Action

# A short exchange: ask focused questions, then give the assessment. Bounded so the FSM
# never lingers (and a restart / command just ends it).
MAX_TURNS = 4

Turn = dict[str, str]  # {"role": "user" | "assistant", "text": ...}

# Built from the SHARED persona core (``dbaylo.persona``) so the interview speaks as the same expert
# assistant as the consultation and the general chat — only role-specialised for history-taking.
INTAKE_PERSONA = (
    persona.IDENTITY + "\n"
    "Right now you are conducting a careful symptom intake (history-taking), like a doctor's "
    "first conversation. The user has a health complaint. Across a SHORT exchange, ask FOCUSED "
    "clarifying questions — where exactly, the character of the symptom, when it started and how "
    "long, severity, what makes it better or worse, associated symptoms, relevant "
    "history/medication. Ask only a small batch (2–4 questions) per message, not an overwhelming "
    "list. Connect the "
    "complaint to their real history when it fits ('памʼятаю, у тебе були камені в нирках — біль у "
    "попереку може бути повʼязаний; чи віддає вбік?') and target your questions accordingly — do "
    "not guess blindly. When you have enough — or when told few exchanges remain — give your "
    "assessment: what the picture MAY suggest (cautiously, 'може бути повʼязано з…', never a "
    "definite diagnosis), practical self-care, and clearly WHEN to see a doctor and which kind.\n"
    + persona.GROUNDING
    + "\n"
    + persona.SAFETY_BOUNDARY
    + "\n"
    + persona.FORMATTING_LIGHT
    + "\n"
    + NATURAL_VOICE
)


@dataclass(frozen=True)
class IntakeReply:
    """One intake turn's reply, and whether the intake is finished."""

    text: str
    done: bool


# Appended to the prompt ONLY when the caller has confirmed the complaint is minor + low-acuity
# (triage MONITOR + otc_amenable). Owner-authorized rail-#1 relaxation: the bot may NAME OTC options
# but NEVER a dose (assert_safe_output enforces it), grounded in the user's Rx meds for interaction.
_OTC_CLAUSE = (
    "This complaint is MINOR and low-acuity. You MAY ALSO briefly name 1-3 well-known OVER-THE-"
    "COUNTER (no-prescription) options people commonly use for it, as GENERAL INFO. Do NOT use the "
    "clinical word 'безрецептурні' — phrase it naturally ('звичайні аптечні засоби', 'те, що є в "
    "аптеці'). NEVER a dose, NEVER 'приймай' / 'take this'; tell the user to confirm the choice "
    "and dose with a pharmacist. The user currently takes these prescription meds: {meds}. If a "
    "OTC option could interact with any of them, add a brief plain caution to check with a "
    "pharmacist (no definitive verdict). If the complaint could actually be serious or connects to "
    "a condition they track, do NOT suggest OTC — steer to a doctor instead. Do NOT tell the user "
    "to press a button or emoji, and do NOT add your own 'це інформація, не призначення' "
    "disclaimer line — one is appended automatically; mention the pharmacist naturally, once."
)


# Physical-complaint stems with word boundaries (Unicode-aware). The "біль" alternative
# uses a lookahead so it does NOT fire on "більше / більший" (more/bigger). Kept deliberately wide
# (it only decides whether to START the gated interview): besides pain/nausea/fever it catches
# pressure/heaviness ("тисне", "важкіст"), region+sensation ("поперек"), colic/spasm/aching, and
# "камінь/камені" (kidney/gallstone) — the phrasings that slipped past the narrow pain vocabulary.
_COMPLAINT_RE = re.compile(
    r"\b(?:бол(?:ить|ять|ю|іло|яч)|біль(?!ш)|ниє|нудит|нудот|блюва|температур|гарячк|"
    r"лихоман|запаморо|паморо|кашл|кашель|нежит|висип|свербіж|свербить|набряк|печія|"
    r"задишк|задих|серцебитт|слабкіст|пронос|діаре|закреп|оніміння|поколюван|запален|"
    r"тисне|важкіст|поперек|камінь|камен|спазм|кольк|колик|ломот|ломит|різь)",
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


def _prompt(
    transcript: list[Turn], *, triage_level: str, exchanges_left: int, context: str = ""
) -> str:
    lines = []
    if context:
        lines.append(context)
        lines.append("")
    lines += [
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
    context: str = "",
    allow_otc: bool = False,
    meds: str = "",
    runner: object = run_claude,
    model: str | None = None,
) -> IntakeReply:
    """Produce the next intake message for a transcript ending in the user's latest turn.

    Runs the deterministic triage backstop, then the (guarded) LLM interview; a high
    escalation always leads the reply and is never softened. ``context`` is an optional grounded
    patient profile (problems + recent analyses) the interview connects the complaint to.
    ``allow_otc`` (owner-authorized, set by the caller ONLY at triage MONITOR for an OTC-amenable
    complaint) lets the reply also name безрецептурні options — never a dose (``assert_safe_output``
    still enforces it); ``meds`` is the user's Rx-med list for an interaction caution.
    """
    user_turns = sum(1 for t in transcript if t.get("role") == "user")
    done = user_turns >= MAX_TURNS

    decision = screen(_accumulated_user_text(transcript))
    triage = decision.triage
    triage_level = triage.action.name if triage is not None else Action.MONITOR.name
    lead = _safety_lead(decision.source, decision)

    prompt = _prompt(
        transcript,
        triage_level=triage_level,
        exchanges_left=0 if done else MAX_TURNS - user_turns,
        context=context,
    )
    # A red flag overrides any OTC offer — never suggest self-care when escalating.
    if allow_otc and lead is None:
        prompt = f"{prompt}\n\n{_OTC_CLAUSE.format(meds=meds or '—')}"

    body = locale.INTAKE_FALLBACK
    model = model or get_settings().claude_chat_model or None  # the (optional) sharper chat model
    try:
        result = await runner(  # type: ignore[operator]
            prompt,
            append_system_prompt=INTAKE_PERSONA,
            model=model,
        )
    except ClaudeUnavailable:
        result = None
    if result is not None and result.ok and result.text.strip():
        try:
            # Drop a model-added disclaimer ('я не лікар' / 'це інформація, не призначення') — the
            # canonical P.S. is appended once, so a self-disclaimer would just duplicate it.
            body = assert_safe_output(strip_self_disclaimer(result.text.strip()))
        except ValueError:
            body = locale.INTAKE_FALLBACK

    combined = f"{lead}\n\n{body}" if lead else body
    return IntakeReply(text=f"{assert_safe_output(combined)}\n\n{DISCLAIMER}", done=done)
