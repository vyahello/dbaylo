"""Group a user's analytes into broad clinical categories (blood / urine / biochemistry /
hormones / other) for the cross-lab dynamics browser.

Pure and deterministic — no LLM, no DB, no network. The category is decided from the panel
the lab printed the row under (``section``), with a small analyte-name fallback for rows that
carry no section (e.g. a single-analyte ДІЛА report). Imaging / descriptive documents (МРТ/УЗД)
are not analytes; they are handled separately as the ``imaging`` category by the caller.

Extend the keyword tables as new panels / analytes appear.
"""

from __future__ import annotations

BLOOD = "blood"
URINE = "urine"
BIOCHEM = "biochem"
HORMONES = "hormones"
SEMEN = "semen"  # spermogram / male-fertility panel — its own specimen, never confused with blood
OTHER = "other"
IMAGING = "imaging"  # narrative documents (set by the caller, not by categorize())

# Display order of the categories in the browser.
CATEGORY_ORDER: tuple[str, ...] = (BLOOD, URINE, BIOCHEM, HORMONES, SEMEN, OTHER, IMAGING)

# Panel-name keyword -> category (checked first, on the printed section). Order matters:
# spermogram before everything (a semen "Еритроцити" must not read as blood), then
# "біохім"/"сеч" before the generic "кров" so "Біохімічний аналіз крові" is biochem, not blood.
_SECTION_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("спермограм", SEMEN),
    ("еякулят", SEMEN),
    ("сперм", SEMEN),
    ("сеч", URINE),
    ("біохім", BIOCHEM),
    ("гормон", HORMONES),
    ("тиреоїд", HORMONES),
    ("кров", BLOOD),
    ("гематолог", BLOOD),
    ("загальний аналіз", BLOOD),
)

# Analyte-name keyword -> category (fallback when a row has no section).
_ANALYTE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("сперматозоїд", "еякулят", "спермі", "сперматогенез", "акросом"), SEMEN),
    (
        (
            "гемоглобін",
            "еритроцит",
            "лейкоцит",
            "тромбоцит",
            "шое",
            "гематокрит",
            "нейтрофіл",
            "лімфоцит",
            "моноцит",
            "базофіл",
            "еозинофіл",
            "ретикулоцит",
        ),
        BLOOD,
    ),
    (
        (
            "натрій",
            "калій",
            "хлор",
            "кальцій",
            "магній",
            "фосфор",
            "глюкоза",
            "холестерин",
            "білірубін",
            "алт",
            "аст",
            "ггт",
            "лужна фосфатаза",
            "креатинін",
            "сечовина",
            "сечова кислота",
            "залізо",
            "феритин",
            "білок",
            "альбумін",
            "ліпопротеїд",
            "тригліцерид",
            "амілаза",
            "црб",
            "с-реактивний",
        ),
        BIOCHEM,
    ),
    (
        (
            "ттг",
            "т3",
            "т4",
            "тиреотроп",
            "тироксин",
            "пролактин",
            "кортизол",
            "тестостерон",
            "естрадіол",
            "прогестерон",
            "інсулін",
            "паратгормон",
        ),
        HORMONES,
    ),
)


def categorize(section: str | None, analyte: str) -> str:
    """Best clinical category for one analyte row, from its panel then its name."""
    s = (section or "").casefold()
    for keyword, category in _SECTION_KEYWORDS:
        if keyword in s:
            return category
    a = analyte.casefold()
    for keywords, category in _ANALYTE_KEYWORDS:
        if any(keyword in a for keyword in keywords):
            return category
    return OTHER
