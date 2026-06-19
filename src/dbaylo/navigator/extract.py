"""Claude HTML-extraction FALLBACK — prompt + a pure defensive parser.

Used only when a source's deterministic parser returns nothing (likely a layout
change). This module holds the persona and a defensive JSON parser; it does **not**
import the LLM client. The actual ``run_claude`` call lives in
:mod:`dbaylo.navigator.pipeline` — the single navigator module allowed to reach the
LLM, and only after :func:`dbaylo.safety.gate.screen` has cleared the user text.

Anything the model returns is treated as untrusted: prices are sanity-checked and
marked ``auto_read=True`` so the rendered output tells the user to verify. The model
can never make a price "official"; on any malformed output the parser yields ``[]``.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from dbaylo.navigator.types import MedPrice

# Internal (English) persona. STRICT JSON, no fabrication.
EXTRACTION_PERSONA = (
    "You extract medicine prices from a Ukrainian pharmacy search-results HTML page. "
    "Return ONLY a JSON array (no prose, no code fences). Each element: "
    '{"name": str, "price": number (UAH), "pharmacy": str, "url": str|null}. '
    "Copy values verbatim from the page. If you cannot find a clear price for an item, "
    "omit it. NEVER invent a price, a pharmacy, or a product that is not on the page. "
    "If the page has no prices, return []."
)

# Reject implausible prices that suggest a parse error / hallucination (UAH).
_MIN_PRICE = Decimal("1")
_MAX_PRICE = Decimal("1000000")


def _coerce_price(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value).replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return price if _MIN_PRICE <= price <= _MAX_PRICE else None


def _load_json_array(text: str) -> list[Any]:
    """Parse a JSON array, tolerating code fences / surrounding prose (fail-soft)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped[stripped.find("[") :] if "[" in stripped else stripped
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("["), stripped.rfind("]")
        if start == -1 or end <= start:
            return []
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return []
    return data if isinstance(data, list) else []


def parse_prices_json(text: str, *, source: str) -> list[MedPrice]:
    """Parse the model's JSON into sanity-checked, auto_read=True MedPrices."""
    results: list[MedPrice] = []
    for item in _load_json_array(text):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        price = _coerce_price(item.get("price"))
        if not name or price is None:
            continue
        url = item.get("url")
        results.append(
            MedPrice(
                name=name,
                price=price,
                pharmacy=str(item.get("pharmacy", "")).strip(),
                source=source,
                url=url if isinstance(url, str) and url else None,
                auto_read=True,
            )
        )
    return results
