"""The deterministic analyte categorizer behind the cross-lab dynamics browser."""

from __future__ import annotations

import pytest

from dbaylo.companion import grouping


@pytest.mark.parametrize(
    ("section", "analyte", "expected"),
    [
        # Panel name decides first (biochem/urine before the generic "кров").
        ("Загальний аналіз крові", "Гемоглобін", grouping.BLOOD),
        ("Біохімічний аналіз крові", "АЛТ", grouping.BIOCHEM),
        ("Загальний аналіз сечі", "Лейкоцити", grouping.URINE),
        ("Мікроскопія осаду сечі", "Епітелій", grouping.URINE),
        # Spermogram is its own specimen: even "Еритроцити" there is NOT blood.
        ("Спермограма", "Еритроцити", grouping.SEMEN),
        ("Спермограма", "Лейкоцити", grouping.SEMEN),
        # No section -> fall back to the analyte name (e.g. a single-analyte ДІЛА report).
        (None, "Натрій", grouping.BIOCHEM),
        (None, "Гемоглобін", grouping.BLOOD),
        (None, "ТТГ", grouping.HORMONES),
        (None, "Кількість сперматозоїдів", grouping.SEMEN),  # semen-specific name
        # The categories that used to dump into "Інше": tumour markers, serology/ПЛР, coagulation,
        # endocrine — now each has a meaningful home (decided from a non-clinical "Імунохімія"-type
        # section by the analyte name).
        ("Пакет №7 (ПСА)", "Простат-специфічний антиген загальний (ПСА)", grouping.MARKERS),
        ("Імунохімія", "fPSA%", grouping.MARKERS),
        ("Біо/імунохімія", "Вірус гепатиту C (HCV), антитіла сумарні", grouping.INFECTION),
        (None, "Коронавірус (SARS-CoV-2), антитіла IgM", grouping.INFECTION),
        (None, "ДНК CMV (Цитомегаловірус), REAL TIME ПЛР", grouping.INFECTION),
        ("Система гемостазу", "D-димер", grouping.COAGULATION),
        ("Альдостерон-ренінове співвідношення (АРС)", "Альдостерон", grouping.HORMONES),
        (None, "Ренін, активний", grouping.HORMONES),
        (None, "Щось небачене", grouping.OTHER),
    ],
)
def test_categorize(section, analyte, expected) -> None:
    assert grouping.categorize(section, analyte) == expected


def test_imaging_is_not_produced_by_categorize() -> None:
    # Imaging is set by the caller (narrative reports), never inferred from an analyte row.
    assert grouping.categorize("МРТ", "опис") != grouping.IMAGING
