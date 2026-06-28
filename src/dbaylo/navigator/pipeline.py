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
from dbaylo.config import get_settings
from dbaylo.labs.extraction import Runner
from dbaylo.labs.humanize import strip_markup, strip_self_disclaimer
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
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


# --- Smart web-search price agent (the bot path) --------------------------------
# Mirrors companion.consult.find_clinics: a gate-screened, guard-checked Claude WebSearch agent.
# It web-searches REAL Ukrainian pharmacy pages for the named drug (fixing obvious misspellings —
# "ношпа" -> the product "Но-шпа"), prefers the doctor's exact dosage when given, and returns a few
# real options with prices + clickable links, in the user's city when known. The deterministic
# regex sources (lookup_drug_price) stay for --dry-run / offline tests. Rails kept: the gate owns
# escalation, the named-drug boundary is enforced by run_price, output passes the navigator guard,
# prices are framed as approximate ("перевір за посиланням"), no dose/diagnosis/skip-doctor, no
# pharmacy ranking. Sources: per the owner, ALL public pharmacy sites may be cited (incl.
# tabletki.ua / apteki.ua) — this is search-result citation of public pages, not endpoint scraping.
PRICE_AGENT_PERSONA = (
    "You are Дбайло helping the user find the PRICE of a NAMED medication in Ukrainian online "
    "pharmacies. You CAN use web search — USE IT to return REAL, current prices, never invented. "
    "For each medicine you are given (its name, and an OPTIONAL dosage/strength): work out the "
    "real product, fixing an obvious misspelling (e.g. 'ношпа' is the product 'Но-шпа'); prefer "
    "the EXACT dosage when one is given (e.g. 'Но-шпа 40 мг'); if NO dosage is given, take the "
    "first / most common form. Search well-known UA pharmacy aggregators and pharmacy sites "
    "(tabletki.ua, apteka911.ua, doc.ua, mypharmacy.com.ua, apteki.ua, pharmacy chains). Give a "
    "few price "
    "options, cheapest first, each with: the product name + dosage, the price in грн, the pharmacy "
    "or site, and a clickable [текст](https://url) link to that exact page. If the user gave a "
    "CITY, prefer offers available or deliverable there and say so; otherwise give national online "
    "prices.\n"
    "Reply EXCLUSIVELY in natural Ukrainian, addressing the user as 'ти'. BE HONEST: prices change "
    "and differ between pharmacies — tell the user these are approximate and to VERIFY at the link "
    "before buying. If you cannot find a clear price for a medicine, say so for THAT medicine — "
    "never invent a number, a pharmacy, or a link.\n"
    "FORMATTING: for each medicine, a short *Назва* header line in *asterisks* for bold, then up "
    "to 4 '• ' option lines, each ending with a [текст](https://url) link. No other markup (no "
    "**double**, #, ---, backticks, raw < >).\n"
    "NEVER: diagnose; advise WHICH drug to take or HOW MUCH to take; tell the user they can skip a "
    "doctor; call a pharmacy 'the best' or rank one as #1 (just list prices). Do NOT add your own "
    "'я не лікар' / disclaimer line — it is appended automatically.\n" + NATURAL_VOICE
)


def _price_query(items: list[tuple[str, str | None]], *, city: str | None) -> str:
    """Build the Ukrainian web-search request from (drug, dose?) pairs + an optional city."""
    drugs = "\n".join(f"- {name}" + (f" {dose}" if dose else "") for name, dose in items)
    city_line = (
        locale.NAV_PRICE_QUERY_CITY.format(city=city) if city else locale.NAV_PRICE_QUERY_NO_CITY
    )
    return f"{city_line}\n{locale.NAV_PRICE_QUERY_INTRO}\n{drugs}"


async def find_prices_web(
    items: list[tuple[str, str | None]],
    *,
    city: str | None = None,
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """Web-search REAL prices for one or more named meds (the bot path). Gate-screened first (a red
    flag escalates), guard-checked, with a deterministic fallback. Returns formatted Ukrainian text
    (light markup kept for HTML) + disclaimer. ``items`` = (drug name, dose/strength or None)."""
    if not items:
        return f"{assert_safe_navigator_output(locale.NAV_NO_RESULTS)}\n\n{DISCLAIMER}"
    query = _price_query(items, city=city)
    if (short_circuit := _gate(query)) is not None:
        return short_circuit.text  # a symptom in a med name -> triage leads, verbatim
    try:
        result = await runner(
            query,
            append_system_prompt=PRICE_AGENT_PERSONA,
            allowed_tools=["WebSearch"],
            model=model,
            timeout_s=get_settings().claude_interpret_timeout_s,
        )
    except ClaudeUnavailable:
        result = None
    if result is None or not result.ok or not result.text.strip():
        return f"{assert_safe_navigator_output(locale.NAV_NO_RESULTS)}\n\n{DISCLAIMER}"
    body = strip_self_disclaimer(result.text.strip())
    try:
        # The guard reads marker-stripped text; ranking/superlatives/skip-doctor/diet still fail.
        assert_safe_navigator_output(strip_markup(body))
    except ValueError:
        return f"{assert_safe_navigator_output(locale.NAV_NO_RESULTS)}\n\n{DISCLAIMER}"
    return f"{body}\n\n{DISCLAIMER}"


async def run_price(
    text: str,
    *,
    fetcher: Fetcher = fetch,
    ceiling_registry: CeilingRegistry | None = None,
    use_llm_fallback: bool = False,
    use_web_agent: bool = False,
    agent_runner: Runner = run_claude,
    city: str | None = None,
    dose: str | None = None,
) -> NavResult:
    """/price — price an explicitly named drug (gated; never picks a drug).

    With ``use_web_agent`` (the bot path) the lookup is the smart Claude WebSearch agent
    (:func:`find_prices_web`) — real prices + links, the doctor's ``dose`` and the user's ``city``
    folded in. The deterministic source-scrape path (``use_llm_fallback`` / ``fetcher``) stays for
    ``--dry-run`` and offline tests.
    """
    if (short_circuit := _gate(text)) is not None:
        return short_circuit
    if is_drug_recommendation_request(text):
        return NavResult(text=f"{locale.NAV_NAMED_DRUG_ONLY}\n\n{DISCLAIMER}")

    drug = text.strip()
    if use_web_agent:
        body = await find_prices_web([(drug, dose)], city=city, runner=agent_runner)
        return NavResult(text=body)
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
