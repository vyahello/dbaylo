"""Provider aggregation: deterministic label, no ranking, superlative fail-closed."""

from __future__ import annotations

import pytest

from dbaylo.locale import REVIEWS_NOT_OUTCOMES
from dbaylo.navigator.providers import render_providers
from dbaylo.navigator.types import ProviderInfo


def test_render_always_carries_the_label() -> None:
    out = render_providers(
        [
            ProviderInfo(
                name="Доктор А", specialization="кардіолог", location="Київ", nszu_contract=True
            ),
            ProviderInfo(name="Доктор Б", specialization="уролог", nszu_contract=False),
        ]
    )
    assert REVIEWS_NOT_OUTCOMES in out
    assert "має договір із НСЗУ" in out
    assert "без договору з НСЗУ" in out


def test_neutral_review_is_shown_labelled() -> None:
    out = render_providers([ProviderInfo(name="Доктор В", review_note="приймає вчасно, уважний")])
    assert "приймає вчасно" in out
    assert REVIEWS_NOT_OUTCOMES in out


def test_superlative_review_fails_closed() -> None:
    # A patient note that reads as a superlative provider recommendation is rejected,
    # not surfaced as authority (rail #4).
    with pytest.raises(ValueError):
        render_providers([ProviderInfo(name="Доктор Г", review_note="найкращий хірург міста")])
