"""Data carriers for the L4 price & НСЗУ navigator.

Deliberately small and honest: every field is something we *fetched* or computed,
never a guess. Two enums encode the two rail-#4 truthfulness boundaries in the type
system itself — :class:`CeilingStatus` has a ``NO_CEILING`` member (so we can say
"no regulated ceiling" rather than fabricate "overpriced"), and :class:`CoverageInfo`
exposes only ``may_be_covered`` (there is no field that can assert a categorical
"free"). If the type can't express the claim, the LLM can't fabricate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True)
class MedPrice:
    """One fetched price for a named medicine from one source."""

    name: str
    price: Decimal
    pharmacy: str
    source: str  # adapter name (e.g. "mypharmacy")
    url: str | None = None
    auto_read: bool = False  # True when read by the Claude fallback (-> "перевір")


class CeilingStatus(StrEnum):
    """Result of a price-ceiling comparison against the МОЗ regulated list.

    ``NO_CEILING`` is first-class on purpose: most drugs are not on the
    reimbursement list, and the honest answer is "no regulated ceiling exists",
    never an invented "overpriced".
    """

    WITHIN = "within"
    ABOVE = "above"
    NO_CEILING = "no_ceiling"


@dataclass(frozen=True)
class CeilingCheck:
    status: CeilingStatus
    limit: Decimal | None = None  # the граничная ціна, only when status != NO_CEILING


@dataclass(frozen=True)
class CoverageInfo:
    """НСЗУ coverage signal. Facility-level only — never a categorical "free".

    ``may_be_covered`` means contracted facilities exist for the relevant package;
    the only user-facing claim is "may be free under ПМГ — verify".
    """

    may_be_covered: bool
    verify_url: str


@dataclass(frozen=True)
class ProviderInfo:
    """Transparently aggregated provider attributes (rail #4).

    Reviews are carried as reviews, never as authority. The "reviews, not outcomes"
    label is attached by the deterministic render template, not stored here.
    """

    name: str
    specialization: str | None = None
    location: str | None = None
    nszu_contract: bool | None = None
    review_note: str | None = None  # shown verbatim, labelled as patient opinion
    url: str | None = None


@dataclass(frozen=True)
class NavResult:
    """What a navigator entry point returns: the rendered Ukrainian text + context."""

    text: str
    prices: list[MedPrice] = field(default_factory=list)
    unavailable_sources: tuple[str, ...] = ()
    short_circuited: bool = False  # True when the safety gate handled the turn
