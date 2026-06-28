"""Deterministic detection of a FREE-FORM price request in chat — pure (no LLM/DB/network).

So a plain message like "знайди мені ліки Но-шпа у Львові і покажи ціни" or "скільки коштує
парацетамол" is routed to the price agent instead of the general companion. Detection is
intentionally specific (a cost/buy phrase, not a bare "ціна"), so it does not steal ordinary chat;
the agent still extracts the named drug (and refuses a symptom-based pick) and asks the user to name
a medicine when none is given, so a rare false-positive degrades gracefully.
"""

from __future__ import annotations

# Specific cost / buy phrasings. A bare "ціна" is deliberately NOT a trigger; "ціна на …",
# "покажи ціни", "знайди ліки", "скільки коштує", etc. are.
_PRICE_TRIGGERS: tuple[str, ...] = (
    "скільки кошт",
    "скільки буде кошт",
    "почім",
    "почому",
    "по чім",
    "вартіст",  # вартість / вартості
    "де купити",
    "де придбати",
    "де замовити",
    "де дешевше",
    "ціна на",
    "ціни на",
    "ціну на",
    "по ціні",
    "знайди цін",
    "знайти цін",
    "пошукай цін",
    "перевір цін",
    "покажи цін",
    "дізнайся цін",
    "знайди ліки",
    "знайди мені ліки",
    "пошукай ліки",
    "скільки за",
)


def is_price_request(text: str) -> bool:
    """True when ``text`` reads as a request for a medicine's price (route to the price agent)."""
    low = (text or "").casefold()
    return any(trigger in low for trigger in _PRICE_TRIGGERS)
