"""Deterministic Ukrainian-city detection — so the clinic finder never asks for a city the user
already named ("де зробити X у Львові?" → Львів). Major cities with their common case forms, matched
WHOLE-WORD (a stem inside another word never false-positives). Pure: no LLM, no DB, no network.

Only used inside the clinic-finding flow, where a city word almost always IS a city — so the few
mildly ambiguous names (Рівне) are safe enough; truly ambiguous ones are omitted and just asked.
"""

from __future__ import annotations

import re

# canonical name -> lowercased declension forms (nominative / genitive / dative / locative / …).
# Oblast centres + the biggest cities; extend as needed.
CITY_FORMS: dict[str, tuple[str, ...]] = {
    "Київ": ("київ", "києва", "києву", "києві", "києвом"),
    "Львів": ("львів", "львова", "львову", "львові", "львовом"),
    "Харків": ("харків", "харкова", "харкову", "харкові", "харковом"),
    "Одеса": ("одеса", "одеси", "одесі", "одесу", "одесою"),
    "Дніпро": ("дніпро", "дніпра", "дніпру", "дніпрі", "дніпром"),
    "Запоріжжя": ("запоріжжя", "запоріжжі", "запоріжжям"),
    "Вінниця": ("вінниця", "вінниці", "вінницю", "вінницею"),
    "Полтава": ("полтава", "полтави", "полтаві", "полтаву", "полтавою"),
    "Чернігів": ("чернігів", "чернігова", "чернігові", "черніговом"),
    "Черкаси": ("черкаси", "черкас", "черкасах", "черкасам"),
    "Житомир": ("житомир", "житомира", "житомирі", "житомиром"),
    "Суми": ("суми", "сумах", "сумам", "сумами"),
    "Рівне": ("рівне", "рівного", "рівному", "рівним"),
    "Івано-Франківськ": (
        "івано-франківськ",
        "івано-франківська",
        "івано-франківську",
        "франківськ",
        "франківська",
        "франківську",
    ),
    "Тернопіль": ("тернопіль", "тернополя", "тернополі", "тернополем"),
    "Луцьк": ("луцьк", "луцька", "луцьку", "луцьком"),
    "Ужгород": ("ужгород", "ужгорода", "ужгороді", "ужгородом"),
    "Хмельницький": ("хмельницький", "хмельницького", "хмельницькому"),
    "Чернівці": ("чернівці", "чернівців", "чернівцях", "чернівцям"),
    "Кропивницький": ("кропивницький", "кропивницького", "кропивницькому"),
    "Миколаїв": ("миколаїв", "миколаєва", "миколаєві", "миколаєвом"),
    "Херсон": ("херсон", "херсона", "херсоні", "херсоном"),
    "Маріуполь": ("маріуполь", "маріуполя", "маріуполі", "маріуполем"),
}

_WORD_RE = re.compile(r"[a-zа-яіїєґ'-]+", re.IGNORECASE)


def parse_city(text: str | None) -> str | None:
    """The canonical city named in ``text`` (in any common case form), or ``None`` if none is found
    — matched on whole words, so a city stem inside a longer word does not trigger."""
    if not text:
        return None
    words = set(_WORD_RE.findall(text.casefold()))
    for canonical, forms in CITY_FORMS.items():
        if any(form in words for form in forms):
            return canonical
    return None
