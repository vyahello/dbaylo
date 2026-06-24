"""Deterministic Ukrainian-city detection — so the clinic finder never re-asks for a city the user
already named ("де зробити X у Львові?")."""

from __future__ import annotations

import pytest

from dbaylo.companion.cities import parse_city


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("де зробити ударно хвильову для розбиття каменів у Львові?", "Львів"),  # the reported bug
        ("здати аналіз сечі в Києві", "Київ"),
        ("я з Одеси", "Одеса"),
        ("УЗД нирок у Франківську", "Івано-Франківськ"),  # short colloquial form
        ("Дніпро", "Дніпро"),
        ("у Чернівцях є?", "Чернівці"),
        ("УЗД нирок", None),  # no city named -> the flow asks
        ("", None),
        (None, None),
    ],
)
def test_parse_city(text, expected) -> None:
    assert parse_city(text) == expected


def test_a_city_stem_inside_a_longer_word_does_not_false_positive() -> None:
    # Whole-word match: "сумнів"/"сумка" must NOT read as Суми; "київський" is not "Київ".
    assert parse_city("я маю сумнів і сумку") is None
    assert parse_city("київський торт смачний") is None  # adjective, not the city form
