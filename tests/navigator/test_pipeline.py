"""Navigator entry points: gated, named-drug only, ceiling-aware, dry-run."""

from __future__ import annotations

from dbaylo.navigator.ceiling import CeilingRegistry
from dbaylo.navigator.pipeline import _fixture_fetcher, main, run_coverage, run_price
from dbaylo.triage.safety import DISCLAIMER


async def test_symptom_input_short_circuits_to_triage_no_fetch() -> None:
    result = await run_price("температура і озноб", fetcher=_fixture_fetcher)
    assert result.short_circuited
    assert result.prices == []  # nothing was fetched
    assert DISCLAIMER in result.text


async def test_coverage_command_arg_is_also_gated() -> None:
    # A command argument is user text — not a trusted bypass.
    result = await run_coverage("болить нирка що робити")
    assert result.short_circuited


async def test_price_refuses_to_pick_a_drug_for_a_condition() -> None:
    result = await run_price("ліки для нирок", fetcher=_fixture_fetcher)
    assert result.prices == []
    assert "конкретно названі ліки" in result.text


async def test_price_lists_named_drug_and_flags_ceiling() -> None:
    registry = CeilingRegistry.from_rows([("Парацетамол", "40.00")])  # cheapest 45.50 > 40
    result = await run_price("Парацетамол", fetcher=_fixture_fetcher, ceiling_registry=registry)
    assert len(result.prices) == 2
    assert "Вище за граничну" in result.text
    assert DISCLAIMER in result.text


async def test_price_no_ceiling_is_honest() -> None:
    result = await run_price("Парацетамол", fetcher=_fixture_fetcher)  # empty ceiling registry
    assert "немає регульованої граничної ціни" in result.text


async def test_coverage_known_package_says_may_be_free() -> None:
    result = await run_coverage("пологи")
    assert not result.short_circuited
    assert "ПМГ" in result.text
    assert "перевір" in result.text.lower()
    assert DISCLAIMER in result.text


def test_dry_run_cli_runs_without_network() -> None:
    assert main(["--dry-run", "Парацетамол"]) == 0
