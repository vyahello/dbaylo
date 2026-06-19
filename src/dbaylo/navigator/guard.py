"""Navigator output guard — rail #4 (no clinical-outcome claims) encoded in code.

Composes the rails that apply to navigator text and adds the rail-#4 superlative
check:

* no "skip the doctor" reassurance (rail #3) and no restrictive-diet prescription
  (rail #6) — reused from :mod:`dbaylo.triage.safety`;
* :func:`contains_superlative_recommendation` — reject superlative clinical
  recommendations about a *named provider* ("найкращий хірург", "оперуйтесь у …",
  "гарантований результат"). Each pattern needs a superlative/ranking *and* a
  provider noun, so neutral copy passes.

**Why not the dose-directive check:** navigator output cites *named drug products*
("Парацетамол №10 таблеток", "500 мг") in a price listing — that is data citation,
not a dosing directive, and would false-positive on the dose-form patterns. The
navigator never *advises* a dose (it lists named products and prices), so the
dose-directive guard does not apply here — mirroring the long-standing "guard what
Дбайло says as a directive, not data it cites" principle.

:func:`is_drug_recommendation_request` is the named-drug boundary (rail #1): "/price"
prices an explicitly named medicine; it never picks a drug for a symptom/condition
("ліки для нирок" is refused). :func:`assert_provider_labeled` is the LAST net — the
"reviews, not outcomes" label is attached deterministically by the render template
(:mod:`dbaylo.navigator.providers`), not by the model.
"""

from __future__ import annotations

import re

from dbaylo import locale
from dbaylo.triage.safety import contains_diet_prescription, contains_forbidden_reassurance

_SUPERLATIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in locale.NAV_SUPERLATIVE_PATTERNS
)
_RECOMMENDATION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in locale.NAV_RECOMMENDATION_REQUEST_PATTERNS
)


def contains_superlative_recommendation(text: str) -> str | None:
    """Return the first superlative provider-recommendation match, else ``None``."""
    for pattern in _SUPERLATIVE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def assert_safe_navigator_output(text: str) -> str:
    """Validate navigator output text, returning it unchanged.

    Rejects (rail #3) "skip the doctor" reassurance, (rail #6) restrictive-diet
    prescriptions, and (rail #4) superlative provider recommendations. Raises
    :class:`ValueError` on a violation. (Dose-directive checking is intentionally
    omitted — see the module docstring.)
    """
    reassurance = contains_forbidden_reassurance(text)
    if reassurance is not None:
        raise ValueError(f"navigator output contains a forbidden reassurance: {reassurance!r}")
    diet = contains_diet_prescription(text)
    if diet is not None:
        raise ValueError(f"navigator output reads as a restrictive-diet prescription: {diet!r}")
    superlative = contains_superlative_recommendation(text)
    if superlative is not None:
        raise ValueError(f"navigator output recommends a provider as 'best': {superlative!r}")
    return text


def assert_provider_labeled(text: str) -> str:
    """Last net: provider output must carry the "reviews, not outcomes" label."""
    if locale.REVIEWS_NOT_OUTCOMES not in text:
        raise ValueError("provider output is missing the 'reviews, not outcomes' label")
    return text


def is_drug_recommendation_request(text: str) -> bool:
    """True when the text asks to *pick* a drug for a condition (-> refuse).

    The named-drug boundary: looking up the price of a named medicine is allowed;
    choosing a medicine for a symptom/diagnosis is not (rail #1).
    """
    return any(pattern.search(text) for pattern in _RECOMMENDATION_PATTERNS)
