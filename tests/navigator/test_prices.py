"""Price orchestration: collect + sort, fail-soft, and the injected LLM fallback."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal

from dbaylo.navigator.fetch import FetchResult
from dbaylo.navigator.prices import PriceLookup, cheapest, lookup_drug_price, render_prices
from dbaylo.navigator.sources.base import MYPHARMACY
from dbaylo.navigator.types import CeilingCheck, CeilingStatus, MedPrice

_CARDS = """
<div class="product-card">
  <a class="product-card__name" href="/p/1">Парацетамол №10 таблетки</a>
  <span class="product-card__price">45,50</span>
  <span class="product-card__pharmacy">Аптека А</span>
</div></div>
<div class="product-card">
  <a class="product-card__name" href="/p/2">Парацетамол №20 таблетки</a>
  <span class="product-card__price">78,00</span>
  <span class="product-card__pharmacy">Аптека Б</span>
</div></div>
"""


def _fetcher(mapping: dict[str, FetchResult]) -> Callable[[str], Awaitable[FetchResult]]:
    async def fetch(url: str) -> FetchResult:
        return mapping.get(url, FetchResult(ok=False, url=url, error="dead"))

    return fetch


async def test_collects_and_sorts_cheapest_first() -> None:
    fetcher = _fetcher(
        {MYPHARMACY.search_url("Парацетамол"): FetchResult(ok=True, url="u", text=_CARDS)}
    )
    lookup = await lookup_drug_price("Парацетамол", fetcher=fetcher, sources=(MYPHARMACY,))
    assert [p.price for p in lookup.prices] == [Decimal("45.50"), Decimal("78.00")]
    assert cheapest(lookup) is not None and cheapest(lookup).price == Decimal("45.50")


async def test_dead_source_is_recorded_not_fatal() -> None:
    lookup = await lookup_drug_price("X", fetcher=_fetcher({}), sources=(MYPHARMACY,))
    assert lookup.prices == []
    assert lookup.unavailable_sources == ("mypharmacy",)


async def test_llm_fallback_used_on_parse_miss() -> None:
    fetcher = _fetcher(
        {MYPHARMACY.search_url("X"): FetchResult(ok=True, url="u", text="<div>нерозпізнано</div>")}
    )

    async def fallback(html: str, source: str) -> list[MedPrice]:
        return [
            MedPrice(name="X", price=Decimal("12"), pharmacy="A", source=source, auto_read=True)
        ]

    lookup = await lookup_drug_price(
        "X", fetcher=fetcher, sources=(MYPHARMACY,), llm_fallback=fallback
    )
    assert len(lookup.prices) == 1 and lookup.prices[0].auto_read


def test_render_no_results_is_honest() -> None:
    text = render_prices(
        "X",
        PriceLookup(prices=[], unavailable_sources=("mypharmacy",)),
        ceiling=CeilingCheck(status=CeilingStatus.NO_CEILING),
    )
    assert "Не вдалося знайти ціни" in text
    assert "mypharmacy" in text  # the unavailable source is named, never faked
