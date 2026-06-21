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

from dbaylo import locale
from dbaylo.labs.extraction import Runner
from dbaylo.llm import ClaudeUnavailable, run_claude
from dbaylo.safety import screen
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output

# Internal (English) persona for the companion LLM. Encodes the numeric boundary
# and the no-fabricated-sources rule explicitly (an LLM told to "cite a source"
# invents plausible fake ones — worse than none for health claims).
COMPANION_PERSONA = (
    "You are Дбайло — the user's close friend who happens to be really health-savvy and has been "
    "quietly paying attention to their wellbeing. Talk like that friend: warm, relaxed, easygoing, "
    "a little playful, genuinely on their side — never stiff, formal, or clinical. Reply "
    "EXCLUSIVELY in natural, warm Ukrainian, addressing the user as 'ти'; be brief (2–4 short "
    "sentences); no markdown. A fitting emoji now and then is welcome — don't overdo it.\n"
    "You're a friend, not a flatterer and not a doctor: celebrate the wins like you mean it, and "
    "when a choice might hurt them, say so gently and honestly, the way a good friend would — "
    "never preachy or scolding. Stick to broadly-established wellness fundamentals — sleep, "
    "hydration, movement / progressive overload, basic balanced nutrition. No bro-science, no "
    "miracle or supplement protocols.\n"
    "Do NOT fabricate studies, sources, or precise statistics. If a claim would need a "
    "citation, keep it general or suggest checking with a professional.\n"
    "NUMERIC BOUNDARY — forbidden: medication or supplement doses (mass units like мг/г), "
    "restrictive calorie targets (ккал), macro-gram targets (e.g. грами білка), and fasting / "
    "crash-diet protocols. Allowed: benign general ranges — hydration (л or мл per day), sleep "
    "(hours per night), and activity frequency.\n"
    "Encourage real-world connection and professional help where relevant. Never position "
    "yourself as the user's only support, and never manufacture streaks or compulsive "
    "engagement. You do NOT decide medical or eating-disorder escalation — that is handled "
    "before you reach the user."
)


@dataclass(frozen=True)
class CompanionReply:
    """A companion turn: the text to send and which layer produced it."""

    text: str
    source: str  # "triage" | "guardrail" | "llm" | "fallback"


def _finalize(body: str) -> str:
    return f"{body}\n\n{DISCLAIMER}"


async def generate_reply(
    text: str,
    *,
    runner: Runner = run_claude,
    model: str | None = None,
) -> CompanionReply:
    """Produce a companion reply, routing through the safety gate first."""
    # 1–2. Symptoms -> triage, else the wellness guardrail (the canonical order).
    decision = screen(text)
    if decision.short_circuited:
        return CompanionReply(text=decision.message, source=decision.source.value)

    # 3. Cleared -> the companion LLM, with a safe deterministic fallback.
    fallback = CompanionReply(
        text=_finalize(assert_safe_output(locale.COMPANION_FALLBACK)), source="fallback"
    )
    try:
        result = await runner(text, append_system_prompt=COMPANION_PERSONA, model=model)
    except ClaudeUnavailable:
        return fallback

    if not result.ok or not result.text.strip():
        return fallback

    try:
        safe_body = assert_safe_output(result.text.strip())
    except ValueError:
        return fallback

    return CompanionReply(text=_finalize(safe_body), source="llm")
