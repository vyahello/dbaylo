"""Source HTML parsing (fail-soft) and the Claude-extraction fallback parser."""

from __future__ import annotations

from decimal import Decimal

from dbaylo.navigator.extract import parse_prices_json
from dbaylo.navigator.sources import DISABLED_SOURCES, ENABLED_SOURCES, RobotsPosture
from dbaylo.navigator.sources.base import MYPHARMACY

_CARDS = """
<div class="product-card">
  <a class="product-card__name" href="/p/1">Парацетамол 500 мг №10 таблетки</a>
  <span class="product-card__price">45,50</span>
  <span class="product-card__pharmacy">Аптека Доброго Дня</span>
</div></div>
<div class="product-card">
  <a class="product-card__name" href="/p/2">Парацетамол 500 мг №20 таблетки</a>
  <span class="product-card__price">78,00</span>
  <span class="product-card__pharmacy">Бажаємо здоров'я</span>
</div></div>
"""


# --- Deterministic parser -------------------------------------------------------


def test_parse_extracts_prices() -> None:
    prices = MYPHARMACY.parse(_CARDS)
    assert [p.price for p in prices] == [Decimal("45.50"), Decimal("78.00")]
    assert prices[0].source == "mypharmacy"
    assert prices[0].pharmacy == "Аптека Доброго Дня"
    assert prices[0].url == "https://mypharmacy.com.ua/p/1"
    assert prices[0].auto_read is False


def test_parse_is_fail_soft() -> None:
    assert MYPHARMACY.parse("") == []
    assert MYPHARMACY.parse("<div>зовсім інша розмітка</div>") == []
    # A card with a name but no price is skipped, never guessed.
    no_price = '<div class="product-card"><a class="product-card__name" href="/x">X</a></div></div>'
    assert MYPHARMACY.parse(no_price) == []


def test_source_registry_postures() -> None:
    assert all(s.posture == RobotsPosture.ALLOWED for s in ENABLED_SOURCES)
    assert "tabletki.ua" in DISABLED_SOURCES and "apteki.ua" in DISABLED_SOURCES


# --- Claude-extraction fallback parser -----------------------------------------


def test_extract_parses_clean_json() -> None:
    out = parse_prices_json(
        '[{"name":"Парацетамол","price":45.5,"pharmacy":"Аптека","url":"http://x"}]', source="s"
    )
    assert len(out) == 1
    assert out[0].price == Decimal("45.5")
    assert out[0].auto_read is True  # marked for "перевір"


def test_extract_strips_code_fences() -> None:
    out = parse_prices_json('```json\n[{"name":"A","price":10}]\n```', source="s")
    assert len(out) == 1


def test_extract_rejects_malformed_and_implausible() -> None:
    assert parse_prices_json("це не json", source="s") == []
    assert parse_prices_json('[{"name":"A","price":0}]', source="s") == []  # below min
    assert parse_prices_json('[{"name":"A","price":99999999}]', source="s") == []  # above max
    assert parse_prices_json('[{"price":10}]', source="s") == []  # missing name
