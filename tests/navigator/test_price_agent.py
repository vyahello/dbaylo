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
    assert runner.kwargs["allowed_tools"] == ["WebSearch"]  # type: ignore[attr-defined]


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


async def test_run_price_web_agent_still_refuses_a_drug_pick() -> None:
    # The named-drug boundary holds on the web path: "ліки від тиску" is a pick request, refused.
    ran = {"x": False}

    async def runner(prompt: str, **kwargs):
        ran["x"] = True
        return SimpleNamespace(ok=True, text="…")

    result = await pipeline.run_price("ліки від тиску", use_web_agent=True, agent_runner=runner)
    assert not ran["x"]
    assert locale.NAV_NAMED_DRUG_ONLY in result.text
