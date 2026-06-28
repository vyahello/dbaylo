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
# Mirrors companion.consult.find_clinics: a gate-screened, guard-checked Claude WebSearch+WebFetch
# agent. It web-searches REAL Ukrainian pharmacy pages for the named drug (fixing obvious
# misspellings — "ношпа" -> the product "Но-шпа"), prefers the doctor's exact dosage when given,
# OPENS each candidate page to confirm it is IN STOCK with a visible price (dropping out-of-stock /
# dead / search-results links), reads "№N" as the PACK SIZE (count per package), sorts cheapest
# first, and ties prices + availability to the user's city when known. The deterministic regex
# sources (lookup_drug_price) stay for --dry-run / offline tests. Rails kept: the gate owns
# escalation, the named-drug boundary is enforced by run_price, output passes the navigator guard,
# prices are framed as approximate ("перевір за посиланням"), no dose/diagnosis/skip-doctor, no
# pharmacy ranking. Sources: per the owner, ALL public pharmacy sites may be cited (incl.
# tabletki.ua / apteki.ua) — this is search-result citation of public pages, not endpoint scraping.
PRICE_AGENT_PERSONA = (
    "You are Дбайло, finding the PRICE of NAMED medications in Ukrainian pharmacies, and you "
    "VERIFY everything. Use web search AND web fetch to return REAL, currently-available prices — "
    "never invented. Go STRAIGHT to the result: do NOT open with filler like 'Маю все, що треба', "
    "'формую відповідь', or 'ось що я знайшов' — just give the prices.\n"
    "For each medicine (its name, and an OPTIONAL dosage): find the real product, fixing an "
    "obvious misspelling ('ношпа' -> the product 'Но-шпа'). The name may carry a leading form "
    "marker like 'Т.' / 'К.' — that only means таблетки / капсули; silently use the plain drug "
    "name and do NOT explain the marker. Prefer the EXACT dosage when given; else the first / most "
    "common form.\n"
    "VERIFY EVERY OPTION — the most important rule. OPEN each candidate page (fetch it) and keep "
    "it ONLY if it is the SPECIFIC pharmacy PRODUCT page for THIS medicine + dosage, shows a "
    "price, and is IN STOCK (в наявності / можна купити / 'в кошик'). DROP anything out of stock, "
    "any 404, a CATEGORY listing (e.g. a '/c.../c...' path), a homepage, or a SEARCH-results page "
    "(e.g. '/search/...'). The single link you give MUST open exactly that product page — the user "
    "has to land on the page for THIS medicine, never a category or a search. If you cannot get "
    "the exact in-stock product URL, do NOT show a misleading link — say you could not confirm it "
    "for that medicine; never guess, never invent a number, a pharmacy, or a link.\n"
    "Sort the options by price — the CHEAPEST in-stock offer FIRST.\n"
    "Pack size: a '№N' you see (e.g. №28) means the pack holds N units. Do NOT write the '№' "
    "notation — write it plainly as 'N таблеток' or 'N капсул' (match the form). Show the pack "
    "size per option, since the price depends on it.\n"
    "CITY: if a city is given, the prices AND availability MUST be for THAT city — only pharmacies "
    "there or delivering there, and say which; never an offer tied to another city. No city -> "
    "national online prices with delivery.\n"
    "Reply EXCLUSIVELY in natural Ukrainian, addressing the user as 'ти'. Prices are approximate "
    "and change between pharmacies — tell the user to confirm at the link before buying.\n"
    "FORMATTING — clean, premium, scannable. For each medicine ONE bold header line "
    "'*Назва · дозування · N таблеток/капсул*', then up to 4 '• ' option lines, cheapest first, "
    "each EXACTLY: '• <ціна> грн — <аптека> (<місто>) — [переглянути](https://exact-product-url)'. "
    "ONE link per line; the link text is only '[переглянути]' (or the pharmacy name) — NEVER paste "
    "a bare domain as if it were a link, and never make the first plain mention look like a link. "
    "No other markup (no **double**, #, ---, backticks, raw < >).\n"
    "NEVER: diagnose; advise WHICH drug to take or HOW MUCH to take; tell the user they can skip a "
    "doctor; call a pharmacy 'the best' or rank one as #1 (just list prices, cheapest first). Do "
    "NOT add your own 'я не лікар' / disclaimer line — it is appended automatically.\n"
    + NATURAL_VOICE
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
    return await _run_price_agent(query, runner=runner, model=model)


async def find_prices_freeform(
    request: str,
    *,
    city: str | None = None,
    history: list[tuple[str, str]] | None = None,
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """Price meds from a FREE-FORM user request ("знайди Но-шпа у Львові, покажи ціни"): the agent
    figures out which NAMED medicine(s) + city the user means and prices them. ``history`` is the
    prior (role, text) turns of an ongoing price conversation — so a follow-up ("а дешевше?", "а в
    Києві?") is answered in context, remembering the drug. Gate-screened first; the named-drug
    boundary still refuses a 'pick a drug for a symptom' request (rail #1)."""
    text = request.strip()
    if not text:
        return f"{assert_safe_navigator_output(locale.NAV_NO_RESULTS)}\n\n{DISCLAIMER}"
    if (short_circuit := _gate(text)) is not None:
        return short_circuit.text
    if is_drug_recommendation_request(text):
        return f"{locale.NAV_NAMED_DRUG_ONLY}\n\n{DISCLAIMER}"
    convo = ""
    if history:
        turns = "\n".join(f"{role}: {body}" for role, body in history)
        convo = locale.NAV_PRICE_FREEFORM_HISTORY.format(history=turns)
    query = convo + locale.NAV_PRICE_FREEFORM_QUERY.format(
        city=city or locale.NAV_PRICE_FREEFORM_NO_CITY, request=text
    )
    return await _run_price_agent(query, runner=runner, model=model)


async def _run_price_agent(query: str, *, runner: Runner, model: str | None) -> str:
    """Run the WebSearch+WebFetch price agent over a prepared query; guard the output, fail soft."""
    try:
        result = await runner(
            query,
            append_system_prompt=PRICE_AGENT_PERSONA,
            # WebFetch lets the agent OPEN each candidate page to confirm it is in stock with a
            # visible price — so it reports verified offers, not dead/out-of-stock/search links.
            allowed_tools=["WebSearch", "WebFetch"],
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
