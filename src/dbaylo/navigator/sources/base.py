"""Source adapter shape + the enabled/disabled registry.

An :class:`HtmlSource` is mostly configuration: a search-URL template plus the
regexes that locate price "cards" in that site's HTML. The deterministic
:meth:`HtmlSource.parse` reads a value or skips a card — it can never invent a
price (a parse miss yields ``[]``, which is the signal for the Claude fallback).

Robots posture is verified per source (see CLAUDE.md / the Stage 4 notes):
tabletki.ua returns 403 on robots.txt (anti-bot) and apteki.ua disallows query
strings (its search), so both are **disabled** for this DETERMINISTIC scraper and
never fetched here — declared for transparency rather than silently scraped.

Note: the bot's primary price path is now the web-search agent
(:func:`dbaylo.navigator.pipeline.find_prices_web`), which the owner authorized to
cite ANY public pharmacy page (incl. tabletki.ua / apteki.ua). That is search-result
citation of public pages a search engine already indexed, NOT hitting their search
endpoints — a different posture from this scraper, which stays narrow and is used only
by the offline ``--dry-run`` / tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import quote

from dbaylo.navigator.types import MedPrice

_PRICE_RE = re.compile(r"(\d[\d\s]*(?:[.,]\d{1,2})?)")


class RobotsPosture(StrEnum):
    ALLOWED = "allowed"  # robots permits the pages we read
    DISABLED = "disabled"  # robots-hostile / disallows search -> never fetched


@dataclass(frozen=True)
class HtmlSource:
    """A deterministic price adapter for one aggregator."""

    name: str
    posture: RobotsPosture
    search_template: str  # e.g. "https://host/search?q={q}"
    card_re: re.Pattern[str]  # matches one product card block (group 0)
    name_re: re.Pattern[str]  # group 1 = product name (searched within a card)
    price_re: re.Pattern[str]  # group 1 = price text
    pharmacy_re: re.Pattern[str] | None = None  # group 1 = pharmacy name
    link_re: re.Pattern[str] | None = None  # group 1 = relative/absolute URL
    link_base: str = ""

    def search_url(self, drug: str) -> str:
        return self.search_template.format(q=quote(drug))

    def parse(self, html: str) -> list[MedPrice]:
        """Extract prices from a search-results page (defensive; skips bad cards)."""
        results: list[MedPrice] = []
        for card_match in self.card_re.finditer(html):
            card = card_match.group(0)
            name_m = self.name_re.search(card)
            price_m = self.price_re.search(card)
            if not name_m or not price_m:
                continue
            price = _parse_price(price_m.group(1))
            if price is None:
                continue
            pharmacy = ""
            if self.pharmacy_re and (pm := self.pharmacy_re.search(card)):
                pharmacy = pm.group(1).strip()
            url = None
            if self.link_re and (lm := self.link_re.search(card)):
                url = self.link_base + lm.group(1) if self.link_base else lm.group(1)
            results.append(
                MedPrice(
                    name=_clean(name_m.group(1)),
                    price=price,
                    pharmacy=pharmacy,
                    source=self.name,
                    url=url,
                )
            )
        return results


def _clean(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def _parse_price(text: str) -> Decimal | None:
    cleaned = text.replace(" ", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


# --- Enabled adapters (robots-permissible) --------------------------------------
# The card/name/price regexes target each site's documented card shape; they are
# fragile by nature (a layout change yields [] -> Claude fallback), and are pinned
# by HTML fixtures in tests/navigator/fixtures.

MYPHARMACY = HtmlSource(
    name="mypharmacy",
    posture=RobotsPosture.ALLOWED,
    search_template="https://mypharmacy.com.ua/search/?text={q}",
    card_re=re.compile(r'<div class="product-card".*?</div>\s*</div>', re.DOTALL),
    name_re=re.compile(r'<a class="product-card__name"[^>]*>(.*?)</a>', re.DOTALL),
    price_re=re.compile(r'<span class="product-card__price"[^>]*>(.*?)</span>', re.DOTALL),
    pharmacy_re=re.compile(r'<span class="product-card__pharmacy"[^>]*>(.*?)</span>', re.DOTALL),
    link_re=re.compile(r'<a class="product-card__name" href="([^"]+)"'),
    link_base="https://mypharmacy.com.ua",
)

DOCUA = HtmlSource(
    name="doc.ua",
    posture=RobotsPosture.ALLOWED,
    search_template="https://doc.ua/apteka/search?query={q}",
    card_re=re.compile(r'<div class="apteka-item".*?</div>\s*</div>', re.DOTALL),
    name_re=re.compile(r'<a class="apteka-item__title"[^>]*>(.*?)</a>', re.DOTALL),
    price_re=re.compile(r'<div class="apteka-item__price"[^>]*>(.*?)</div>', re.DOTALL),
    pharmacy_re=re.compile(r'<div class="apteka-item__shop"[^>]*>(.*?)</div>', re.DOTALL),
    link_re=re.compile(r'<a class="apteka-item__title" href="([^"]+)"'),
    link_base="https://doc.ua",
)

ENABLED_SOURCES: tuple[HtmlSource, ...] = (MYPHARMACY, DOCUA)

# Declared-disabled for transparency — never fetched (verified robots posture).
DISABLED_SOURCES: dict[str, str] = {
    "tabletki.ua": "robots.txt returns HTTP 403 (anti-bot); not fetched",
    "apteki.ua": "robots.txt disallows query strings (its search); not fetched",
}
