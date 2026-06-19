"""Navigator entry points + a --dry-run CLI.

This is the **only navigator module that reaches the LLM**, and it does so only
after :func:`dbaylo.safety.gate.screen` clears the user text — so a symptom input
short-circuits to triage and a disordered signal to the guardrail before any fetch or
model call (the AST choke-point test enforces this). Command arguments are user text
and are screened identically — a command is not a trusted bypass.

``python -m dbaylo.navigator.pipeline --dry-run "<drug>"`` runs the full price
pipeline over a built-in HTML fixture (no network) and prints the result.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dbaylo import locale
from dbaylo.llm import run_claude
from dbaylo.navigator.ceiling import CeilingRegistry, check_ceiling
from dbaylo.navigator.coverage import CoverageRegistry
from dbaylo.navigator.extract import EXTRACTION_PERSONA, parse_prices_json
from dbaylo.navigator.fetch import Fetcher, FetchResult, fetch
from dbaylo.navigator.guard import assert_safe_navigator_output, is_drug_recommendation_request
from dbaylo.navigator.prices import LlmFallback, cheapest, lookup_drug_price, render_prices
from dbaylo.navigator.services import lookup_service
from dbaylo.navigator.types import CeilingCheck, CeilingStatus, MedPrice, NavResult
from dbaylo.safety import screen
from dbaylo.triage.safety import DISCLAIMER


def _gate(text: str) -> NavResult | None:
    """Run the safety gate; return a short-circuit NavResult, or None when cleared."""
    decision = screen(text)
    if decision.short_circuited:
        return NavResult(text=decision.message, short_circuited=True)
    return None


async def _claude_fallback(html: str, source: str) -> list[MedPrice]:
    """The (html, source) -> [MedPrice] Claude fallback (only runs post-gate)."""
    result = await run_claude(html, append_system_prompt=EXTRACTION_PERSONA)
    if not result.ok or not result.text.strip():
        return []
    return parse_prices_json(result.text, source=source)


async def run_price(
    text: str,
    *,
    fetcher: Fetcher = fetch,
    ceiling_registry: CeilingRegistry | None = None,
    use_llm_fallback: bool = False,
) -> NavResult:
    """/price — price an explicitly named drug (gated; never picks a drug)."""
    if (short_circuit := _gate(text)) is not None:
        return short_circuit
    if is_drug_recommendation_request(text):
        return NavResult(text=f"{locale.NAV_NAMED_DRUG_ONLY}\n\n{DISCLAIMER}")

    drug = text.strip()
    fallback: LlmFallback | None = _claude_fallback if use_llm_fallback else None
    lookup = await lookup_drug_price(drug, fetcher=fetcher, llm_fallback=fallback)

    registry = ceiling_registry or CeilingRegistry()
    cheap = cheapest(lookup)
    check = (
        check_ceiling(drug, cheap.price, registry=registry)
        if cheap is not None
        else CeilingCheck(status=CeilingStatus.NO_CEILING)
    )
    body = assert_safe_navigator_output(render_prices(drug, lookup, ceiling=check))
    return NavResult(
        text=f"{body}\n\n{DISCLAIMER}",
        prices=lookup.prices,
        unavailable_sources=lookup.unavailable_sources,
    )


async def run_coverage(text: str, *, registry: CoverageRegistry | None = None) -> NavResult:
    """/coverage — check ПМГ coverage for a service (gated; coverage before price)."""
    if (short_circuit := _gate(text)) is not None:
        return short_circuit
    body = assert_safe_navigator_output(lookup_service(text.strip(), registry=registry))
    return NavResult(text=f"{body}\n\n{DISCLAIMER}")


# --- Dry-run CLI (fixture mode, no network) -------------------------------------

_FIXTURE_HTML = """
<div class="product-card">
  <a class="product-card__name" href="/p/1">Парацетамол 500 мг №10 таблетки</a>
  <span class="product-card__price">45,50</span>
  <span class="product-card__pharmacy">Аптека Доброго Дня</span>
</div></div>
<div class="product-card">
  <a class="product-card__name" href="/p/2">Парацетамол 500 мг №20 таблетки</a>
  <span class="product-card__price">78,00</span>
  <span class="product-card__pharmacy">Бажаємо здоров'я</span>
</div></div>
"""


async def _fixture_fetcher(url: str) -> FetchResult:
    return FetchResult(ok=True, url=url, text=_FIXTURE_HTML, status=200)


async def _dry_run(query: str) -> int:
    result = await run_price(query, fetcher=_fixture_fetcher)
    print(result.text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.navigator.pipeline")
    parser.add_argument("--dry-run", action="store_true", help="fixture mode; no network")
    parser.add_argument("query", help="a drug name to price")
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("only --dry-run is supported from the CLI")
    return asyncio.run(_dry_run(args.query))


if __name__ == "__main__":
    sys.exit(main())
