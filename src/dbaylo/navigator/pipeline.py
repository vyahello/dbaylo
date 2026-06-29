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
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output


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
    "PREFER AGGREGATORS first — tabletki.ua and apteki.ua: one product page there lists MANY "
    "pharmacies' live stock at once, so a single fetch covers lots of options (broad AND fast). "
    "Also doc.ua, mypharmacy.com.ua, apteka911.ua, liki24, e-apteka, pharmacy chains. Use the "
    "aggregator's 'в наявності' (in-stock) filter to find what is actually available.\n"
    "VERIFY EVERY OPTION — the most important rule. OPEN each candidate page (fetch it) and keep "
    "it ONLY if it shows THIS medicine + dosage, a price, and that it is IN STOCK (в наявності / "
    "можна купити / 'в кошик'). DROP anything out of stock, any 404, a homepage, or a SEARCH-"
    "results page (e.g. '/search/...'). A concrete aggregator/pharmacy PRODUCT page (e.g. "
    "apteki.ua/.../product-…) is good; a bare category list is not. The single link you give MUST "
    "open the page for THIS medicine. If you cannot get an exact in-stock product URL, do NOT show "
    "a misleading link — never guess or invent a number, a pharmacy, or a link.\n"
    "BROADEN before giving up: if the medicine is not in stock in the user's city, check NATIONAL "
    "availability (online pharmacies with delivery) and the aggregators' in-stock filter; try the "
    "active substance / brand variants. Do NOT declare it unavailable in all of Ukraine — say only "
    "what you actually checked, and point the user to the aggregator's in-stock filter (and a "
    "back-in-stock alert) for the rest.\n"
    "BE EFFICIENT within your time budget: lead with 1-2 aggregator pages (they cover many "
    "pharmacies) rather than fetching a dozen single sites; return what you have confirmed.\n"
    "Sort the options by price — the CHEAPEST in-stock offer FIRST.\n"
    "Pack size: a '№N' you see (e.g. №28) means the pack holds N units. Do NOT write the '№' "
    "notation — write it plainly as 'N таблеток' or 'N капсул' (match the form). Show the pack "
    "size per option, since the price depends on it.\n"
    "CITY: if a city is given, list offers for THAT city first (pharmacies there or delivering "
    "there, say which). If nothing is in stock there, ALSO give national/online options with "
    "delivery — never leave the user with just 'немає'. No city -> national online prices.\n"
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
            # A tight timeout (not the 10-min interpret one): on a slow/hung search it falls back to
            # "не вдалося" fast instead of leaving the chat on "typing…" for minutes.
            timeout_s=get_settings().claude_price_timeout_s,
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
    """/coverage — deterministic ПМГ coverage for a service (gated; 'may be free — verify'). The bot
    uses the smarter :func:`find_coverage` web agent; this stays for the offline path."""
    if (short_circuit := _gate(text)) is not None:
        return short_circuit
    body = assert_safe_navigator_output(lookup_service(text.strip(), registry=registry))
    return NavResult(text=f"{body}\n\n{DISCLAIMER}")


# --- Smart НСЗУ / ПМГ agent (the bot path) --------------------------------------
# What can be FREE for the user under the Програма медичних гарантій (ПМГ) + «Доступні ліки»: which
# services/meds the state covers, what the user needs (declaration / referral / e-prescription), and
# WHERE (НСЗУ-contracted facilities in their city, web-searched). Honesty rail: NEVER a categorical
# "free" — a deterministic verify caveat (facility/indication-dependent + the НСЗУ hotline 16-77 +
# dashboard) is ALWAYS appended (like the providers label) — even a too-confident answer is hedged.
COVERAGE_AGENT_PERSONA = (
    "You are Дбайло, helping the user learn what medical care can be FREE for them under Ukraine's "
    "Програма медичних гарантій (ПМГ, run by НСЗУ — the state pays a contracted facility so the "
    "patient pays nothing) and the «Доступні ліки» programme (reimbursed — free or discounted — "
    "medicines). Use web search to give REAL, current info, never invented. Go straight to the "
    "answer, no filler preamble.\n"
    "Given a SERVICE / exam / question (or a list of MEDICINES): (1) say whether it is TYPICALLY "
    "covered under ПМГ and roughly which package (primary care with a family doctor, diagnostics "
    "referral, childbirth, emergency, stroke/heart-attack care, mental health, hospital care) — be "
    "concrete about what is usually free vs usually paid; (2) say WHAT THE USER NEEDS to get it "
    "free: a signed declaration (декларація) with a primary-care doctor, a referral (направлення) "
    "for specialists / diagnostics / hospital, an e-prescription (е-рецепт) for «Доступні ліки»; "
    "(3) WHERE: web-search a few REAL facilities in the user's city that have an НСЗУ contract for "
    "this (name + how to confirm). For MEDICINES: check the «Доступні ліки» list — say if the drug "
    "or its active substance is reimbursed and that it needs an e-prescription.\n"
    "STRUCTURE (important — make it scannable, like a smart assistant, not a wall of text): START "
    "with ONE bold bottom-line sentence — the TL;DR a person can read in 2 seconds ('💊 Підсумок: "
    "лише 1 із 7 — безкоштовне за е-рецептом (Симода); решта — платні.' / '🆓 Коротко: УЗД нирок "
    "може бути безкоштовним за ПМГ — потрібні декларація + е-направлення.'). THEN details under "
    "short bold sub-headers. END with a '➡️ Що далі' line giving 1–2 concrete next steps tailored "
    "to THIS user (e.g. 'оформи е-рецепт у сімейного на Симоду; решту порівняй за цінами'). Keep "
    "each section tight — no repetition, no filler.\n"
    "HONESTY (critical): NEVER promise it is definitely free — it depends on the facility, the "
    "medical indication, and a referral. Always frame it as 'може бути безкоштовно за ПМГ — це "
    "треба підтвердити'. Do not invent a specific facility or a guarantee.\n"
    "Reply EXCLUSIVELY in natural Ukrainian, addressing the user as 'ти'. Be practical and "
    "concrete — the goal is that the user actually knows HOW to get free care. FORMATTING: bold "
    "*headers*, '• ' bullets, clickable [текст](https://url) links for facilities / sources. No "
    "other markup (no **double**, #, ---, backticks, raw < >).\n"
    "EFFICIENCY (you have a tight time budget): use only a handful of searches and open very few "
    "pages — answer from search results when you can. For a MEDICINES list, do NOT search for "
    "pharmacies or facilities at all; just check the «Доступні ліки» reimbursement list.\n"
    "NEVER: diagnose; advise WHICH drug to take or a dose; tell the user they can skip a doctor; "
    "call a clinic 'the best'. Do NOT add your own 'я не лікар' / disclaimer line — it is appended "
    "automatically.\n" + NATURAL_VOICE
)


def _coverage_footer() -> str:
    """The deterministic verify caveat appended to EVERY coverage answer — so it never reads as a
    guarantee of 'free' (rail #4: coverage is facility/indication-dependent)."""
    return locale.NAV_COVERAGE_AGENT_FOOTER.format(
        hotline=locale.NSZU_HOTLINE, url=locale.NSZU_DASHBOARD_URL
    )


def _coverage_fallback() -> str:
    return locale.NAV_COVERAGE_FALLBACK.format(
        hotline=locale.NSZU_HOTLINE, url=locale.NSZU_DASHBOARD_URL
    )


async def find_coverage(
    request: str,
    *,
    city: str | None = None,
    is_meds: bool = False,
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """Smart ПМГ/НСЗУ answer (the bot path): what may be FREE, what the user needs, and where —
    web-searched, city-grounded. ``is_meds`` switches to the «Доступні ліки» med-reimbursement query
    (``request`` is then the med list). Gate-screened first; the verify caveat is ALWAYS appended so
    the output is never a categorical 'free' (rail #4); deterministic fallback on any failure."""
    text = request.strip()
    if not text:
        return f"{_coverage_fallback()}\n\n{DISCLAIMER}"
    if (short_circuit := _gate(text)) is not None:
        return short_circuit.text
    if is_meds:
        # «Доступні ліки» is dispensed at ANY participating pharmacy — no facility/city search
        # needed. Keeping the meds query lean (no facility WebFetches) is what keeps it inside the
        # tight timeout (the heavy facility search was timing it out into the fallback).
        query = locale.NAV_COVERAGE_MEDS_QUERY.format(meds=text)
    else:
        city_line = (
            locale.NAV_COVERAGE_QUERY_CITY.format(city=city)
            if city
            else locale.NAV_COVERAGE_QUERY_NO_CITY
        )
        query = locale.NAV_COVERAGE_QUERY.format(city_line=city_line, request=text)
    try:
        result = await runner(
            query,
            append_system_prompt=COVERAGE_AGENT_PERSONA,
            allowed_tools=["WebSearch", "WebFetch"],
            model=model,
            timeout_s=get_settings().claude_price_timeout_s,
        )
    except ClaudeUnavailable:
        result = None
    if result is None or not result.ok or not result.text.strip():
        return f"{_coverage_fallback()}\n\n{DISCLAIMER}"
    body = strip_self_disclaimer(result.text.strip())
    try:
        assert_safe_navigator_output(strip_markup(body))
    except ValueError:
        return f"{_coverage_fallback()}\n\n{DISCLAIMER}"
    return f"{body}\n\n{_coverage_footer()}\n\n{DISCLAIMER}"


# --- OTC self-care agent (the bot path) -----------------------------------------
# OWNER-AUTHORIZED relaxation of rail #1 (personal bot): for a MINOR, low-acuity complaint, name a
# few common OVER-THE-COUNTER (no-prescription) options people use + their prices. Made safe by: the
# caller only reaches this at triage MONITOR (a red flag escalates, never here); the output STILL
# passes assert_safe_output (a NAME is allowed but any DOSE directive hard-fails) AND the navigator
# guard (no skip-doctor); the user's own Rx meds are passed in for an interaction caution; and a
# deterministic footer (info-not-prescription · pharmacist · doctor-if-persists) is always appended.
OTC_PRICE_PERSONA = (
    "You are Дбайло helping with a MINOR, everyday health complaint by naming general OVER-THE-"
    "COUNTER (no-prescription, безрецептурні) options people commonly use for it, and the prices. "
    "Use web search + web fetch for REAL current prices. This is GENERAL INFORMATION, not a "
    "prescription or a diagnosis.\n"
    "Name 2-3 well-known OTC options (by product or active-substance name) that people in Ukraine "
    "buy WITHOUT a prescription for this complaint. Do NOT use the clinical word 'безрецептурні' — "
    "phrase it naturally ('звичайні аптечні засоби', 'те, що є в аптеці'). NEVER state or imply a "
    "DOSE or 'take this' / "
    "'приймай' — just name the options as info; tell the user to confirm the choice AND the dose "
    "with a pharmacist. NEVER suggest a prescription-only medicine.\n"
    "INTERACTIONS: the user already takes these prescription medicines: {meds}. If a named OTC "
    "option commonly interacts with any of them (e.g. NSAIDs with blood-thinners/SSRIs), add a "
    "brief plain caution to check with a pharmacist before combining — do NOT give a definitive "
    "verdict.\n"
    "If the complaint could actually be serious, or connects to a condition the user is tracking, "
    "do NOT push OTC — say it is better to see a doctor.\n"
    "PRICES: for each named option give 1-2 in-stock offers, cheapest first, each "
    "'• <ціна> грн — <аптека> — [переглянути](https://exact-product-url)'; '№N' is the pack "
    "count — write 'N таблеток/капсул', no '№'; for a city, prefer offers there, else national. "
    "If you cannot confirm a price for an option, say so for it — never invent a link or number.\n"
    "TIME BUDGET (important — you price several drugs, do NOT run out of time): work FAST. "
    "Prefer an aggregator page (tabletki.ua / apteki.ua) — ONE page lists many pharmacies' "
    "stock and prices for a drug, so you can price an option with one fetch instead of opening "
    "many shop pages. Do a SMALL number of focused searches; do not exhaustively verify every "
    "candidate link. A reasonable in-stock offer from an aggregator is enough.\n"
    "Reply EXCLUSIVELY in natural Ukrainian, 'ти'. Short bold *headers* per option, '• ' price "
    "lines, clickable links. No other markup. Do NOT add your own 'я не лікар' / disclaimer line — "
    "it is appended automatically.\n" + NATURAL_VOICE
)


def _otc_footer() -> str:
    return locale.OTC_FOOTER


async def find_otc_prices(
    complaint: str,
    *,
    city: str | None = None,
    meds: str = "",
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """For a MINOR complaint: name common OTC options + prices (owner-authorized self-care path).
    Gate-screened FIRST (a red flag → triage, never OTC); output passes BOTH ``assert_safe_output``
    (a drug NAME is fine, any DOSE directive hard-fails) and the navigator guard; an interaction
    caution is grounded in ``meds``; the info-not-prescription footer is always appended. The CALLER
    must only invoke this at triage MONITOR for an OTC-amenable complaint."""
    text = complaint.strip()
    if not text:
        return _otc_footer()  # the OTC footer IS the single disclaimer (no extra P.S.)
    if (short_circuit := _gate(text)) is not None:
        return short_circuit.text  # a red flag in the complaint escalates — never OTC
    query = locale.OTC_QUERY.format(complaint=text, city=city or locale.NAV_PRICE_FREEFORM_NO_CITY)
    persona = OTC_PRICE_PERSONA.format(meds=meds or locale.OTC_NO_MEDS)
    try:
        result = await runner(
            query,
            append_system_prompt=persona,
            allowed_tools=["WebSearch", "WebFetch"],
            model=model,
            # Pricing 2-3 drugs at once needs a longer budget than the single-drug price agent.
            timeout_s=get_settings().claude_otc_timeout_s,
        )
    except ClaudeUnavailable:
        result = None
    if result is None or not result.ok or not result.text.strip():
        return locale.OTC_FALLBACK  # self-contained (mentions pharmacist + doctor)
    body = strip_self_disclaimer(result.text.strip())
    try:
        # The dose guard (assert_safe_output) is the key safeguard: a NAME passes, a DOSE fails.
        clean = strip_markup(body)
        assert_safe_output(clean)
        assert_safe_navigator_output(clean)
    except ValueError:
        return locale.OTC_FALLBACK
    # ONE disclaimer: the OTC footer (info-not-prescription · pharmacist · doctor), no generic P.S.
    return f"{body}\n\n{_otc_footer()}"


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
