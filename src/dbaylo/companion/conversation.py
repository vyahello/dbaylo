"""Companion conversation — natural Ukrainian encouragement, with the two
deterministic safety cores in front of the LLM.

Routing for any free-text turn (the order is the safety contract):

1. **Symptoms -> triage.** If a symptom token is detected, the deterministic
   triage engine produces the reply; the LLM is not called.
2. **Disordered patterns / unsafe goals -> wellness guardrail.** Otherwise, if the
   guardrail raises a concern, its deterministic message is returned.
3. **Otherwise -> the companion LLM.** Every LLM reply passes
   ``assert_safe_output`` and gets the disclaimer; on any trip or failure it falls
   back to a deterministic Ukrainian message.

The LLM never decides escalation — steps 1–2 own that.
"""

from __future__ import annotations

from dataclasses import dataclass

from dbaylo import locale, persona
from dbaylo.labs.extraction import Runner
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
from dbaylo.safety import screen
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output

Turn = dict[str, str]  # {"role": "user" | "assistant", "text": ...}

# Internal (English) persona for the companion LLM. Built from the SHARED persona core
# (``dbaylo.persona``) so general chat speaks as the same expert assistant as the consultation —
# same identity, grounding rule, and safety boundary — only warmer/lighter for everyday talk. The
# numeric boundary and the no-fabricated-sources rule live in the shared SAFETY_BOUNDARY block.
COMPANION_PERSONA = (
    persona.IDENTITY + "\n"
    "Right now you are in everyday conversation with the user — talk like a warm, easygoing friend "
    "who also happens to be their health expert: relaxed, a little playful, genuinely on their "
    "side, never stiff or clinical. For casual chit-chat keep it short and human (2–4 sentences). "
    "The moment they raise something about their health or how they feel, switch into EXPERT mode: "
    "ground in their real data, explain plainly what it MAY mean and WHY (cautiously — 'може бути "
    "повʼязано з…', never a definite diagnosis), how concerning it looks, and the concrete next "
    "steps. Be proactive like a good clinician — when it helps, ask 1–3 focused questions (woven "
    "into a warm reply, not a questionnaire) about how they feel, where/when it bothers them, and "
    "relevant history, so you actually understand their state instead of guessing.\n"
    "Stick to broadly-established wellness fundamentals — sleep, hydration, movement / progressive "
    "overload, basic balanced nutrition. No bro-science, no miracle or supplement protocols. "
    "Celebrate real wins like you mean it; when a choice might hurt them, say so gently and "
    "honestly, never preachy. A fitting emoji now and then is welcome — don't overdo it.\n"
    + persona.GROUNDING
    + "\n"
    + persona.SAFETY_BOUNDARY
    + "\n"
    + persona.FORMATTING_LIGHT
    + "\n"
    + NATURAL_VOICE
)


@dataclass(frozen=True)
class CompanionReply:
    """A companion turn: the text to send and which layer produced it."""

    text: str
    source: str  # "triage" | "guardrail" | "llm" | "fallback"


def _finalize(body: str) -> str:
    return f"{body}\n\n{DISCLAIMER}"


def _build_prompt(text: str, *, context: str, history: list[Turn]) -> str:
    """Assemble the model prompt. With neither prior turns nor grounding it is the bare user text (a
    single-turn reply); otherwise the grounded context and the conversation so far are laid out so
    Дбайло answers the LATEST message in thread — like an assistant who remembers what was said."""
    if not history and not context:
        return text
    lines: list[str] = []
    if context:
        lines += [context, ""]
    if history:
        lines.append("Розмова досі (відповідай на ОСТАННЄ повідомлення користувача):")
        for turn in history:
            who = "Користувач" if turn.get("role") == "user" else "Дбайло"
            lines.append(f"{who}: {turn.get('text', '')}")
        lines.append(f"Користувач: {text}")
    else:
        lines.append(f"Повідомлення користувача: {text}")
    return "\n".join(lines)


async def generate_reply(
    text: str,
    *,
    context: str = "",
    history: list[Turn] | None = None,
    runner: Runner = run_claude,
    model: str | None = None,
) -> CompanionReply:
    """Produce a companion reply, routing through the safety gate first.

    ``context`` is an optional grounded patient profile (problems + recent analyses + a memory of
    earlier talks) the reply draws on when relevant. ``history`` is the recent back-and-forth (prior
    turns, excluding ``text``) so the conversation THREADS instead of answering each line cold.
    """
    # 1–2. Symptoms -> triage, else the wellness guardrail (the canonical order).
    decision = screen(text)
    if decision.short_circuited:
        return CompanionReply(text=decision.message, source=decision.source.value)

    # 3. Cleared -> the companion LLM, with a safe deterministic fallback.
    fallback = CompanionReply(
        text=_finalize(assert_safe_output(locale.COMPANION_FALLBACK)), source="fallback"
    )
    prompt = _build_prompt(text, context=context, history=history or [])
    try:
        result = await runner(prompt, append_system_prompt=COMPANION_PERSONA, model=model)
    except ClaudeUnavailable:
        return fallback

    if not result.ok or not result.text.strip():
        return fallback

    try:
        safe_body = assert_safe_output(result.text.strip())
    except ValueError:
        return fallback

    return CompanionReply(text=_finalize(safe_body), source="llm")
