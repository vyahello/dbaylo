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
    # bare "find me X" search verbs — by the time routing reaches the price intent, symptoms,
    # complaints, clinic/booking and history queries are already handled, so "знайди мені ношпу"
    # here means "find me the drug" (the agent extracts it; asks to name one if none is given).
    "знайди мені",
    "знайди ",
    "знайти ",
    "пошукай ",
    "пошукати ",
    "знайди де купити",
)


def is_price_request(text: str) -> bool:
    """True when ``text`` reads as a request for a medicine's price (route to the price agent)."""
    low = (text or "").casefold()
    return any(trigger in low for trigger in _PRICE_TRIGGERS)


# Continuation phrasings — only consulted while a FRESH price thread exists, so a short follow-up
# like "а дешевше?" / "а в іншій аптеці?" / "а в Києві?" continues the SAME price conversation
# (the drug is remembered from the thread) instead of falling through to general chat.
_FOLLOWUP_TRIGGERS: tuple[str, ...] = (
    "дешевш",
    "інша аптек",
    "іншій аптец",
    "інші аптек",
    "ще аптек",
    "ще варіант",
    "ще десь",
    "ще варто",
    "доставк",
    "самовивіз",
    "наявн",
    "інша пачк",
    "більша пачк",
    "менша пачк",
    "інше дозув",
    "інше місто",
    "в іншому міст",
    "знижк",
    "акці",
    "а в ",
    "а є ",
    "а скільки",
    "а ціна",
    "а де ",
    "а що",
)


def is_price_followup(text: str) -> bool:
    """True when ``text`` reads as a follow-up to an ongoing price conversation (continue the
    thread). Only meaningful while a fresh price thread exists — the caller gates on that."""
    low = (text or "").casefold().strip()
    return any(trigger in low for trigger in _FOLLOWUP_TRIGGERS)


# A question about STATE coverage — what may be FREE under ПМГ / НСЗУ / «Доступні ліки». Routed to
# the smart coverage agent. Specific tokens, so it does not steal ordinary chat.
_COVERAGE_TRIGGERS: tuple[str, ...] = (
    "безкоштовн",
    "безплатн",
    "за державн",
    "коштом держав",
    "держава оплач",
    "держава покрив",
    "пмг",
    "нсзу",
    "медичн гарант",
    "медичних гарант",
    "реімбурс",
    "відшкодув",
    "за декларац",
    "чи покрив",
    "чи безкошт",
    "чи платн",
)


def is_coverage_request(text: str) -> bool:
    """True when ``text`` asks what may be FREE under ПМГ / НСЗУ / «Доступні ліки» (route to the
    coverage agent). Checked before the price intent (more specific)."""
    low = (text or "").casefold()
    if any(trigger in low for trigger in _COVERAGE_TRIGGERS):
        return True
    return "доступн" in low and "лік" in low  # «Доступні ліки» in any case form
