"""Price-ceiling check against the МОЗ regulated-price list.

Source: the МОЗ "граничні оптово-відпускні ціни" dataset — the prices regulated for
drugs **subject to reimbursement** (Доступні ліки / ПМГ). Crucially, this covers only
a *subset* of medicines. For a drug that is not on the list the honest result is
:data:`CeilingStatus.NO_CEILING` — we must never imply a price is normal or inflated
against a ceiling that does not exist (rail #4 / correction #2). A false "overpriced"
is misinformation just like a false "free".

The registry is built from rows of ``(normalized drug name -> limit price)``; tests
and the dry-run supply a small fixture instead of the live CSV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from dbaylo import locale
from dbaylo.navigator.types import CeilingCheck, CeilingStatus


def _normalize(name: str) -> str:
    return " ".join(name.casefold().split())


@dataclass(frozen=True)
class CeilingRegistry:
    """Regulated limit prices keyed by normalized drug name (reimbursement subset)."""

    limits: dict[str, Decimal] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: list[tuple[str, str | Decimal]]) -> CeilingRegistry:
        """Build from ``(name, price)`` rows, skipping anything unparseable (fail-soft)."""
        limits: dict[str, Decimal] = {}
        for name, raw in rows:
            try:
                price = raw if isinstance(raw, Decimal) else Decimal(str(raw).replace(",", "."))
            except (InvalidOperation, ValueError):
                continue
            if name and price > 0:
                limits[_normalize(name)] = price
        return cls(limits)

    def limit_for(self, drug_name: str) -> Decimal | None:
        return self.limits.get(_normalize(drug_name))


def check_ceiling(drug_name: str, price: Decimal, *, registry: CeilingRegistry) -> CeilingCheck:
    """Compare ``price`` against the regulated ceiling for ``drug_name``.

    Returns ``NO_CEILING`` (with no limit) when the drug is not on the regulated
    list — never an invented "overpriced".
    """
    limit = registry.limit_for(drug_name)
    if limit is None:
        return CeilingCheck(status=CeilingStatus.NO_CEILING)
    status = CeilingStatus.ABOVE if price > limit else CeilingStatus.WITHIN
    return CeilingCheck(status=status, limit=limit)


def render_ceiling(check: CeilingCheck) -> str:
    """Render the ceiling result; "no regulated ceiling" is an explicit, honest case."""
    if check.status == CeilingStatus.NO_CEILING or check.limit is None:
        return locale.NAV_CEILING_NONE
    limit = f"{check.limit:.2f}"
    if check.status == CeilingStatus.ABOVE:
        return locale.NAV_CEILING_ABOVE.format(limit=limit)
    return locale.NAV_CEILING_WITHIN.format(limit=limit)
