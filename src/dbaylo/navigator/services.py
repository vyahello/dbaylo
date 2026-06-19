"""Lab/clinic service queries — НСЗУ coverage FIRST, then (later) price.

Rail-aligned ordering: the real saving is coverage, so a service query always
checks the Програма медичних гарантій *before* any price search and surfaces "may be
free under ПМГ — verify". On-demand lab/clinic price comparison is constrained by
robots (synevo.ua / dila.ua disallow query-string search), so the lean build surfaces
coverage and defers a service-price adapter to a future iteration — but the ordering
is structural: nothing fetches a price before coverage is consulted.
"""

from __future__ import annotations

from dbaylo.navigator.coverage import CoverageRegistry, check_coverage, render_coverage


def lookup_service(service: str, *, registry: CoverageRegistry | None = None) -> str:
    """Return the coverage signal for a service (checked before any price)."""
    info = check_coverage(service, registry=registry)
    return render_coverage(info)
