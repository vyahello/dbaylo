"""Coverage (only "may be free") and ceiling (honest "no ceiling") guards."""

from __future__ import annotations

from decimal import Decimal

from dbaylo.locale import NAV_CEILING_NONE, NAV_COVERAGE_MAYBE_FREE
from dbaylo.navigator.ceiling import CeilingRegistry, check_ceiling, render_ceiling
from dbaylo.navigator.coverage import CoverageRegistry, check_coverage, render_coverage
from dbaylo.navigator.types import CeilingStatus, CoverageInfo

# --- Coverage: only ever "may be covered", never categorical "free" -------------


def test_coverage_info_has_no_categorical_free_field() -> None:
    # The type can only express "may be covered" — there is no is_free attribute.
    info = CoverageInfo(may_be_covered=True, verify_url="x")
    assert not hasattr(info, "is_free")


def test_known_pmg_package_may_be_covered() -> None:
    info = check_coverage("пологи у пологовому будинку")
    assert info.may_be_covered
    assert "перевір" in render_coverage(info).lower() or "перевірити" in render_coverage(info)


def test_unknown_service_is_not_claimed_covered() -> None:
    info = check_coverage("масаж спини")
    assert not info.may_be_covered


def test_render_coverage_never_asserts_free_categorically() -> None:
    info = check_coverage("ведення вагітності")
    rendered = render_coverage(info)
    # It says "можливо ... безкоштовно ... перевір", never a bare guarantee.
    assert rendered == NAV_COVERAGE_MAYBE_FREE.format(url=info.verify_url)
    assert "можливо" in rendered.lower()


def test_registry_can_narrow_coverage() -> None:
    registry = CoverageRegistry.from_package_ids(["childbirth"])
    assert check_coverage("інсульт", registry=registry).may_be_covered is False
    assert check_coverage("пологи", registry=registry).may_be_covered is True


# --- Ceiling: regulated subset only; "no ceiling" is explicit -------------------


def _registry() -> CeilingRegistry:
    return CeilingRegistry.from_rows([("Парацетамол", "60.00"), ("Аспірин", "30,50")])


def test_price_within_ceiling() -> None:
    check = check_ceiling("Парацетамол", Decimal("45.50"), registry=_registry())
    assert check.status == CeilingStatus.WITHIN
    assert check.limit == Decimal("60.00")


def test_price_above_ceiling_is_flagged() -> None:
    check = check_ceiling("Парацетамол", Decimal("99.00"), registry=_registry())
    assert check.status == CeilingStatus.ABOVE


def test_unregulated_drug_has_no_ceiling_not_overpriced() -> None:
    check = check_ceiling("Якісь рідкісні ліки", Decimal("500.00"), registry=_registry())
    assert check.status == CeilingStatus.NO_CEILING
    assert check.limit is None
    assert render_ceiling(check) == NAV_CEILING_NONE


def test_ceiling_registry_skips_unparseable_rows() -> None:
    registry = CeilingRegistry.from_rows([("Good", "10"), ("Bad", "not-a-number"), ("Zero", "0")])
    assert registry.limit_for("Good") == Decimal("10")
    assert registry.limit_for("Bad") is None
    assert registry.limit_for("Zero") is None
