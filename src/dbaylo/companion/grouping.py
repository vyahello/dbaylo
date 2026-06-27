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
MARKERS = "markers"  # tumour / prostate markers (ПСА, СА-125, РЕА, АФП …)
INFECTION = "infection"  # serology / ПЛР: hepatitis, HIV, COVID, EBV, CMV, antibodies …
COAGULATION = "coagulation"  # haemostasis: D-dimer, INR, fibrinogen, APTT …
SEMEN = "semen"  # spermogram / male-fertility panel — its own specimen, never confused with blood
OTHER = "other"
IMAGING = "imaging"  # narrative documents (set by the caller, not by categorize())

# Display order of the categories in the browser.
CATEGORY_ORDER: tuple[str, ...] = (
    BLOOD,
    URINE,
    BIOCHEM,
    HORMONES,
    MARKERS,
    INFECTION,
    COAGULATION,
    SEMEN,
    OTHER,
    IMAGING,
)

# Panel-name keyword -> category (checked first, on the printed section). Order matters:
# spermogram before everything (a semen "Еритроцити" must not read as blood), then the specific
# panels (гемостаз/біохім/сеч) before the generic "кров" so "Біохімічний аналіз крові" is biochem.
# NOTE: a non-clinical method section (e.g. "Імунохімія", "Біо/імунохімія") is deliberately NOT a
# keyword — it carries hormones AND markers AND serology, so we let the analyte name decide instead.
_SECTION_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("спермограм", SEMEN),
    ("еякулят", SEMEN),
    ("сперм", SEMEN),
    ("гемостаз", COAGULATION),
    ("згорт", COAGULATION),
    ("коагул", COAGULATION),
    ("сеч", URINE),
    ("біохім", BIOCHEM),
    ("гормон", HORMONES),
    ("тиреоїд", HORMONES),
    ("кров", BLOOD),
    ("гематолог", BLOOD),
    ("загальний аналіз", BLOOD),
)

# Analyte-name keyword -> category (fallback when a section is missing or non-clinical). Specific
# panels first; the broad biochemistry list is LAST so a marker/hormone is never misread as biochem.
_ANALYTE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("сперматозоїд", "еякулят", "спермі", "сперматогенез", "акросом"), SEMEN),
    (
        (
            "пса",
            "psa",
            "fpsa",
            "простат-специфічн",
            "са-125",
            "са 125",
            "ca-125",
            "ca 125",
            "са-15-3",
            "са 15-3",
            "са-19-9",
            "са 19-9",
            "ca 19-9",
            "раеа",
            "рэа",
            "cea",
            "афп",
            "afp",
            "не-4",
            "he4",
            "онкомаркер",
        ),
        MARKERS,
    ),
    (
        (
            "гепатит",
            "hbsag",
            "hbs ag",
            "hbv",
            "hcv",
            "віл",
            "hiv",
            "sars-cov",
            "covid",
            "коронавірус",
            "антитіл",
            "antibod",
            "igg",
            "igm",
            "iga",
            "плр",
            "пцр",
            "real time",
            "днк ",
            "рнк ",
            "епштейн",
            "епштайн",
            "ebv",
            "cmv",
            "цитомегал",
            "герпес",
            "hhv",
            "уреаплазм",
            "ureaplasma",
            "хламід",
            "chlamyd",
            "токсоплазм",
            "краснух",
            "rubella",
            "сифіліс",
            "treponema",
        ),
        INFECTION,
    ),
    (
        (
            "д-димер",
            "d-димер",
            "d-dimer",
            "протромбін",
            "мно",
            "inr",
            "фібриноген",
            "ачтв",
            "тромбіновий",
            "антитромбін",
            "пті",
            "птв",
        ),
        COAGULATION,
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
            "альдостерон",
            "ренін",
            "дгеа",
            "дгея",
            "dhea",
            "фсг",
            "лютеїнізуюч",
            "соматотроп",
            "кальцитонін",
            "17-он",
            "17-oh",
            "статеві гормони",
        ),
        HORMONES,
    ),
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


def category_emoji(name: str) -> str:
    """The clinical-group emoji ('🩸 ' / '🔬 ' / '⚗️ ' / …) for a stored analyte / concern / goal
    name, re-derived by ``categorize(name, name)`` (so a printed-panel prefix or a '(сеча)' tag in
    the name is honoured). Empty for a non-lab name (other / imaging) — it has no specimen group.

    The single source of truth for "which аналіз does this name belong to", shared by every list
    that tags its items (Під наглядом / Відкладені / Вирішені / Цілі), so they read identically.
    """
    from dbaylo import locale  # leaf module (pure strings); kept local to keep grouping light

    category = categorize(name, name)
    if category in (OTHER, IMAGING):
        return ""
    emoji = locale.CATEGORY_NAMES.get(category, "").split(" ", 1)[0]
    return f"{emoji} " if emoji else ""
