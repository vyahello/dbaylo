"""НСЗУ coverage check — "may be free under ПМГ", and only that.

Source: the НСЗУ open dataset of facilities contracted under the Програма медичних
гарантій (data.gov.ua, updated weekly). That data is facility/package-level, **not**
per-procedure, so the only truthful output is "this kind of service may be free at a
contracted facility — verify". By design there is **no method that returns a
categorical "free"** (rail #4 / correction #1): the type system can't express it, so
nothing downstream can fabricate it.

The primary signal is a conservative map of well-known ПМГ packages
(``locale.PMG_PACKAGE_KEYWORDS``). An optional :class:`CoverageRegistry` (built from
the contracted-facilities CSV) can refine it per region; absent that, an unknown
service simply yields "couldn't determine — check the dashboard".
"""

from __future__ import annotations

from dataclasses import dataclass

from dbaylo import locale
from dbaylo.navigator.types import CoverageInfo


@dataclass(frozen=True)
class CoverageRegistry:
    """Optional refinement: ПМГ package labels with at least one contracted facility.

    ``covered_packages`` holds the English package ids (keys of
    ``PMG_PACKAGE_KEYWORDS``) for which the loaded data shows a contract. When
    empty, only the package-keyword heuristic is used.
    """

    covered_packages: frozenset[str] = frozenset()

    @classmethod
    def from_package_ids(cls, ids: list[str]) -> CoverageRegistry:
        return cls(frozenset(ids))


def _match_package(service: str) -> str | None:
    """Return the ПМГ package id whose keywords appear in ``service``, else ``None``."""
    lowered = service.casefold()
    for package_id, keywords in locale.PMG_PACKAGE_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return package_id
    return None


def check_coverage(service: str, *, registry: CoverageRegistry | None = None) -> CoverageInfo:
    """Return a coverage signal for ``service`` — only ever "may be covered".

    ``may_be_covered`` is True when the service maps to a known ПМГ package (and,
    if a registry is supplied, that package has a contracted facility). It is never
    a guarantee — the message always tells the user to verify.
    """
    package_id = _match_package(service)
    may_be_covered = package_id is not None
    if may_be_covered and registry is not None and registry.covered_packages:
        may_be_covered = package_id in registry.covered_packages
    return CoverageInfo(may_be_covered=may_be_covered, verify_url=locale.NSZU_DASHBOARD_URL)


def render_coverage(info: CoverageInfo) -> str:
    """Render the coverage signal as Ukrainian text (never a categorical "free")."""
    if info.may_be_covered:
        return locale.NAV_COVERAGE_MAYBE_FREE.format(url=info.verify_url)
    return locale.NAV_COVERAGE_UNKNOWN.format(url=info.verify_url)
