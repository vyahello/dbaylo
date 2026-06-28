"""The smart web-search price agent (the bot path): gate-screened, guard-checked, fail-soft.

Mirrors the clinic-finder pattern. A fake runner stands in for the Claude WebSearch call, so these
stay offline and deterministic. The deterministic source-scrape path (test_prices.py) is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

from dbaylo import locale
from dbaylo.navigator import pipeline
from dbaylo.triage.safety import DISCLAIMER


def _runner(text: str, *, ok: bool = True):
    async def run(prompt: str, **kwargs):  # accepts append_system_prompt/allowed_tools/model/...
        run.prompt = prompt  # type: ignore[attr-defined]
        run.kwargs = kwargs  # type: ignore[attr-defined]
        return SimpleNamespace(ok=ok, text=text)

    return run


async def test_find_prices_web_returns_guarded_text_with_links() -> None:
    runner = _runner("*Но-шпа 40 мг*\n• 50 грн — Аптека X [doc.ua](https://doc.ua/no-shpa)")
    out = await pipeline.find_prices_web([("ношпа", "40 мг")], city="Львів", runner=runner)
    assert "Но-шпа" in out and "doc.ua" in out
    assert out.endswith(DISCLAIMER)
    # The agent saw the exact dosage + the city in its request.
    assert "40 мг" in runner.prompt and "Львів" in runner.prompt  # type: ignore[attr-defined]
    # The agent gets WebFetch too, so it can OPEN each page to confirm in-stock + price.
    assert runner.kwargs["allowed_tools"] == ["WebSearch", "WebFetch"]  # type: ignore[attr-defined]


async def test_find_prices_web_no_city_searches_nationally() -> None:
    runner = _runner("*Парацетамол*\n• 30 грн [mypharmacy](https://mypharmacy.com.ua/p)")
    out = await pipeline.find_prices_web([("парацетамол", None)], runner=runner)
    assert "Парацетамол" in out
    assert "україні" in runner.prompt.lower()  # type: ignore[attr-defined]


async def test_find_prices_web_symptom_in_a_name_short_circuits_to_triage() -> None:
    # A red flag smuggled into the "drug" field reaches the gate FIRST — triage, never a search.
    called = {"ran": False}

    async def runner(prompt: str, **kwargs):
        called["ran"] = True
        return SimpleNamespace(ok=True, text="…")

    out = await pipeline.find_prices_web([("температура і озноб", None)], runner=runner)
    assert not called["ran"]  # the model is never reached
    assert "лікар" in out.lower()  # triage guidance


async def test_find_prices_web_fails_closed_on_unsafe_output() -> None:
    # A model reply that says "skip the doctor" must NOT reach the user — fall back to NO_RESULTS.
    runner = _runner("Ціна 50 грн. Можеш не йти до лікаря, просто купи.")
    out = await pipeline.find_prices_web([("ношпа", None)], runner=runner)
    assert "не йти до лікаря" not in out
    assert locale.NAV_NO_RESULTS in out


async def test_find_prices_web_empty_or_unavailable_is_honest() -> None:
    assert locale.NAV_NO_RESULTS in await pipeline.find_prices_web([])
    runner = _runner("", ok=False)
    assert locale.NAV_NO_RESULTS in await pipeline.find_prices_web([("ношпа", None)], runner=runner)


async def test_run_price_web_agent_routes_through_find_prices_web() -> None:
    runner = _runner("*Но-шпа 40 мг*\n• 50 грн [tabletki](https://tabletki.ua/no-shpa)")
    result = await pipeline.run_price(
        "ношпа", use_web_agent=True, agent_runner=runner, city="Київ", dose="40 мг"
    )
    assert "Но-шпа" in result.text
    assert "ношпа 40 мг" in runner.prompt and "Київ" in runner.prompt  # type: ignore[attr-defined]


async def test_find_prices_freeform_extracts_from_a_sentence() -> None:
    runner = _runner(
        "*Но-шпа 40 мг · 24 таблетки*\n• 95 грн — Аптека X (Львів) — [переглянути](https://doc.ua/p/x)"
    )
    out = await pipeline.find_prices_freeform(
        "знайди мені ліки Но-шпа у Львові і покажи ціни", city="Львів", runner=runner
    )
    assert "Но-шпа" in out and out.endswith(DISCLAIMER)
    # The whole free-form request + the city are handed to the agent to parse.
    assert "Но-шпа" in runner.prompt and "Львів" in runner.prompt  # type: ignore[attr-defined]


async def test_find_prices_freeform_refuses_a_symptom_based_pick() -> None:
    ran = {"x": False}

    async def runner(prompt: str, **kwargs):
        ran["x"] = True
        return SimpleNamespace(ok=True, text="…")

    out = await pipeline.find_prices_freeform("підбери ліки від тиску і ціну", runner=runner)
    assert not ran["x"]
    assert pipeline.locale.NAV_NAMED_DRUG_ONLY in out


async def test_find_prices_freeform_symptom_short_circuits() -> None:
    out = await pipeline.find_prices_freeform("температура і озноб, скільки коштує")
    assert "лікар" in out.lower()


def test_is_price_request_detects_cost_phrasings() -> None:
    from dbaylo.navigator import priceintent

    assert priceintent.is_price_request("знайди мені ліки Но-шпа у Львові і покажи ціни")
    assert priceintent.is_price_request("скільки коштує парацетамол?")
    assert priceintent.is_price_request("де купити зопіклон")
    assert not priceintent.is_price_request("як ти сьогодні?")
    assert not priceintent.is_price_request("у мене болить голова")  # not a price ask


async def test_find_coverage_appends_the_verify_footer_and_disclaimer() -> None:
    runner = _runner(
        "*УЗД нирок*\n• Може покривати ПМГ за направленням.\n• [НСЗУ](https://nszu.gov.ua)"
    )
    out = await pipeline.find_coverage("чи безкоштовне УЗД нирок", city="Львів", runner=runner)
    assert "ПМГ" in out and out.endswith(DISCLAIMER)
    assert locale.NSZU_HOTLINE in out  # the always-appended verify caveat (never a bald "free")
    assert "Львів" in runner.prompt  # type: ignore[attr-defined]


async def test_find_coverage_meds_uses_the_dostupni_liky_query() -> None:
    runner = _runner("*Бісопролол* — так, за «Доступними ліками» (е-рецепт).")
    out = await pipeline.find_coverage("- Бісопролол", is_meds=True, runner=runner)
    assert "Доступними ліками" in out or "Доступні ліки" in runner.prompt  # type: ignore[attr-defined]
    assert locale.NSZU_HOTLINE in out


async def test_find_coverage_symptom_short_circuits() -> None:
    out = await pipeline.find_coverage("температура і озноб, чи безкоштовно")
    assert "лікар" in out.lower()


async def test_find_coverage_falls_back_when_agent_unavailable() -> None:
    runner = _runner("", ok=False)
    out = await pipeline.find_coverage("пологи", runner=runner)
    assert locale.NSZU_HOTLINE in out and out.endswith(DISCLAIMER)  # honest fallback, still useful


def test_is_coverage_request_detects_pmg_questions() -> None:
    from dbaylo.navigator import priceintent

    assert priceintent.is_coverage_request("чи безкоштовне УЗД нирок?")
    assert priceintent.is_coverage_request("це покриває ПМГ?")
    assert priceintent.is_coverage_request("де безплатно здати аналізи")
    assert priceintent.is_coverage_request("мої ліки за доступними ліками?")
    assert not priceintent.is_coverage_request("скільки коштує парацетамол")
    assert not priceintent.is_coverage_request("як ти?")


async def test_find_otc_prices_names_options_with_footer_and_grounds_meds() -> None:
    runner = _runner("*Парацетамол*\n• 30 грн — Аптека X — [переглянути](https://apteki.ua/p)")
    out = await pipeline.find_otc_prices(
        "болить голова", city="Львів", meds="буспірон", runner=runner
    )
    assert "Парацетамол" in out
    assert locale.OTC_FOOTER in out and out.endswith(DISCLAIMER)  # info-not-prescription footer
    # The user's Rx meds are in the system prompt for the interaction caution.
    assert "буспірон" in runner.kwargs["append_system_prompt"]  # type: ignore[attr-defined]


async def test_find_otc_prices_blocks_a_dose_directive() -> None:
    # The no-dose guard (assert_safe_output) is the key safeguard: a NAME is fine, a DOSE is not.
    runner = _runner("*Парацетамол*\n• по 2 таблетки 3 рази на день")
    out = await pipeline.find_otc_prices("болить голова", runner=runner)
    assert "таблетки" not in out  # failed closed
    assert locale.OTC_FALLBACK in out


async def test_find_otc_prices_blocks_skip_the_doctor() -> None:
    runner = _runner("Візьми парацетамол. Можеш не йти до лікаря.")
    out = await pipeline.find_otc_prices("болить голова", runner=runner)
    assert "не йти до лікаря" not in out
    assert locale.OTC_FALLBACK in out


async def test_find_otc_prices_red_flag_short_circuits_to_triage() -> None:
    # Defense in depth: even if a red flag reaches it, the gate escalates — never OTC.
    out = await pipeline.find_otc_prices("температура і озноб")
    assert "лікар" in out.lower()


async def test_run_price_web_agent_still_refuses_a_drug_pick() -> None:
    # The named-drug boundary holds on the web path: "ліки від тиску" is a pick request, refused.
    ran = {"x": False}

    async def runner(prompt: str, **kwargs):
        ran["x"] = True
        return SimpleNamespace(ok=True, text="…")

    result = await pipeline.run_price("ліки від тиску", use_web_agent=True, agent_runner=runner)
    assert not ran["x"]
    assert locale.NAV_NAMED_DRUG_ONLY in result.text
