"""Med-price orchestration for a single, explicitly named drug.

Iterates the enabled (robots-permissible) sources, fetches each on demand, and
parses deterministically; on a parse miss it calls the injected Claude fallback
(layout drift). Everything fails soft — a dead source is recorded in
``unavailable_sources``, never crashes, never fabricates. No source = no price.

Scope boundary (rail #1): the input is a *drug name*. Picking a drug for a symptom
is refused upstream (:func:`dbaylo.navigator.guard.is_drug_recommendation_request`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from dbaylo import locale
from dbaylo.navigator.ceiling import render_ceiling
from dbaylo.navigator.fetch import Fetcher
from dbaylo.navigator.sources import ENABLED_SOURCES, HtmlSource
from dbaylo.navigator.types import CeilingCheck, MedPrice

# (html, source_name) -> prices. Wired to run_claude + extract in the pipeline.
LlmFallback = Callable[[str, str], Awaitable[list[MedPrice]]]


@dataclass(frozen=True)
class PriceLookup:
    prices: list[MedPrice] = field(default_factory=list)
    unavailable_sources: tuple[str, ...] = ()


async def lookup_drug_price(
    drug: str,
    *,
    fetcher: Fetcher,
    llm_fallback: LlmFallback | None = None,
    sources: tuple[HtmlSource, ...] = ENABLED_SOURCES,
) -> PriceLookup:
    """Fetch + parse prices for ``drug`` across the enabled sources (fail-soft)."""
    prices: list[MedPrice] = []
    unavailable: list[str] = []
    for source in sources:
        result = await fetcher(source.search_url(drug))
        if not result.ok:
            unavailable.append(source.name)
            continue
        parsed = source.parse(result.text)
        if not parsed and llm_fallback is not None:
            parsed = await llm_fallback(result.text, source.name)
        prices.extend(parsed)

    prices.sort(key=lambda p: p.price)
    return PriceLookup(prices=prices, unavailable_sources=tuple(unavailable))


def cheapest(lookup: PriceLookup) -> MedPrice | None:
    return lookup.prices[0] if lookup.prices else None


def render_prices(drug: str, lookup: PriceLookup, *, ceiling: CeilingCheck) -> str:
    """Render the price results as Ukrainian text (deterministic; no fabrication)."""
    lines: list[str] = []
    if not lookup.prices:
        lines.append(locale.NAV_NO_RESULTS)
    else:
        lines.append(locale.NAV_PRICE_HEADER.format(drug=drug))
        for price in lookup.prices[:5]:
            item = locale.NAV_PRICE_ITEM.format(
                name=price.name, price=f"{price.price:.2f}", pharmacy=price.pharmacy or "—"
            )
            if price.auto_read:
                item = f"{item} {locale.NAV_AUTO_READ}"
            lines.append(item)
            if price.url:
                lines.append(locale.NAV_PRICE_LINK.format(url=price.url))
        lines.append("")
        lines.append(render_ceiling(ceiling))

    if lookup.unavailable_sources:
        lines.append("")
        lines.append(
            locale.NAV_SOURCE_UNAVAILABLE.format(sources=", ".join(lookup.unavailable_sources))
        )
    return "\n".join(lines)
