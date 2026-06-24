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
from dbaylo.labs.humanize import strip_markup, strip_self_disclaimer
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
from dbaylo.navigator.guard import contains_superlative_recommendation
from dbaylo.safety import GateSource, screen
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output
from dbaylo.triage.types import Action

Turn = dict[str, str]  # {"role": "user" | "assistant", "text": ...}

# Keep only the recent exchange in the model's view — enough for a coherent consultation without
# the prompt growing unbounded across a long back-and-forth.
MAX_CONTEXT_TURNS = 8

CONSULT_PERSONA = (
    "You are Дбайло, the user's PERSONAL medical assistant — an experienced, caring expert who "
    "knows this person's health picture (their lab data, dates, and tracked concerns are given to "
    "you). Talk one on one, like a real doctor texting a friend they care about.\n"
    "STYLE — fast, natural, ALIVE: keep each reply SHORT and conversational, like a real chat, NOT "
    "a written report. Usually 2–4 short sentences, or a few quick bullets. Get STRAIGHT to the "
    "point — no preamble, no padding, no restating what you already said. Ask ONE focused question "
    "at a time (a real conversation is back-and-forth), never a questionnaire. Go into more detail "
    "ONLY if the user asks for it.\n"
    "GROUND every claim in the DATA you are given (the patient profile + the subject) — never "
    "invent a value, reference range, trend, or finding that is not there; if something is "
    "missing, say so. Use the dates: notice if a key result is OLD and factor that in ('останній "
    "такий аналіз був N тому').\n"
    "Be a real expert: explain plainly what it means FOR THIS CASE, what an out-of-range value MAY "
    "point to (cautiously — 'може свідчити про…', never a definite diagnosis), and the concrete "
    "next step (which doctor, how soon).\n"
    "WHERE TO DO IT: if the user asks where to get an exam/analysis done (or you suggest one), "
    "give transparent, practical guidance — most lab tests (аналіз крові/сечі) are done at any "
    "лабораторія or поліклініка; imaging (УЗД/КТ/МРТ) at a діагностичний центр or лікарня. You may "
    "name common options as NEUTRAL examples, but NEVER rank them, call any one 'найкраща', say "
    "'оперуйся у…', or promise a result. Add that they can check possible НСЗУ coverage at "
    + locale.NSZU_DASHBOARD_URL
    + " ('може бути безкоштовно — перевір', never a definite 'безкоштовно'). Stay in the SAME "
    "conversation — use what was already discussed, do not restart.\n"
    "When you recommend a recheck / exam / visit with a timeframe, briefly OFFER to set a reminder "
    "(the 🔔 Нагадати button is under your message) — ONE short line, do not push.\n"
    "Reply EXCLUSIVELY in natural, warm Ukrainian, addressing the user as 'ти'. FORMATTING (light "
    "— a few per message, never on every word): wrap a key term or the bottom line in single "
    "*asterisks* for bold, and a gentle caveat in _underscores_ for italic; use '• ' for bullets. "
    "No other markup (no **double**, #, ---, backticks, < >).\n"
    "A deterministic safety check runs alongside you and decides urgency: you are told its level "
    "and must NEVER go below it or imply the user can skip care. NEVER give: a definitive "
    "diagnosis; a medication, supplement, or any dose; calorie/macro/fasting numbers; fabricated "
    "studies, sources, or statistics. Do not use the phrases 'все добре', 'усе добре', "
    "'ти здоровий', 'ти здорова', 'не хвилюйся', 'нічого страшного' — describe the data instead. "
    "Do NOT add your own 'я не лікар' / disclaimer line — it is appended automatically.\n"
    + NATURAL_VOICE
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
        # Drop a model-added 'я не лікар' duplicate, KEEP the light *bold*/_italic_ markup (rendered
        # to HTML at send time), and guard the marker-STRIPPED text so a forbidden phrase can't hide
        # behind a marker (e.g. "все *добре*").
        candidate = strip_self_disclaimer(result.text.strip())
        guarded = strip_markup(candidate)
        try:
            assert_safe_output(guarded)
            # Rail #4: the consult may mention clinics, so it must never rank a provider or call one
            # 'best' / 'operate here' / promise a result — reject that like the navigator does.
            if contains_superlative_recommendation(guarded) is not None:
                raise ValueError("superlative provider recommendation")
            body, source = candidate, "llm"
        except ValueError:
            body, source = locale.CONSULT_FALLBACK, "fallback"

    if lead is not None:
        source = decision.source.value
    combined = f"{lead}\n\n{body}" if lead else body
    assert_safe_output(strip_markup(combined))  # belt-and-suspenders; parts are already safe
    return ConsultReply(text=f"{combined}\n\n{DISCLAIMER}", source=source)
