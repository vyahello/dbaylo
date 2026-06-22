"""LLM layer — the only place that shells out to the `claude` binary.

Isolated from the deterministic engine on purpose (discovery L2): trends are
computed in code, never by the model. The model is invoked here for lab
extraction and for humanizing already-computed numbers — nothing else.

All calls go through the `claude` binary via subprocess (Claude Code OAuth),
never the Anthropic SDK.
"""

from dbaylo.llm.client import ClaudeResult, ClaudeUnavailable, run_claude

# Shared voice guidance appended to every persona so the Ukrainian output reads like a real person,
# not a chatbot. Distilled from the "Signs of AI writing" checklist — it targets the specific tells
# that make generated text obvious, while leaving each persona's own rules (brevity, the safety
# boundary, section structure) untouched.
NATURAL_VOICE = (
    "Sound like a real person texting a friend, not an article or a chatbot. Write plain, direct "
    "Ukrainian and say each thing once. Vary sentence length: mix short lines with longer ones; "
    "do not pad every sentence to the same shape. Avoid these tells: sycophantic openers "
    "('Чудове питання!', 'Звичайно!'); announcing what you are about to say ('Розберімо', 'Ось що "
    "важливо'); filler and stacked hedging; forcing ideas into groups of exactly three; "
    "'не просто …, а …' / 'це не …, це …' framing; slogan-like closers and aphorisms; inflated "
    "adjectives ('неймовірний', 'потужний'); and manufactured drama from stacking short fragments. "
    "Do not decorate every line with an emoji. (Normal Ukrainian тире punctuation is fine.)"
)

__all__ = ["ClaudeResult", "ClaudeUnavailable", "NATURAL_VOICE", "run_claude"]
