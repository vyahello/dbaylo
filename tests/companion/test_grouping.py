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
        # No section -> fall back to the analyte name (e.g. a single-analyte ДІЛА report).
        (None, "Натрій", grouping.BIOCHEM),
        (None, "Гемоглобін", grouping.BLOOD),
        (None, "ТТГ", grouping.HORMONES),
        (None, "Щось небачене", grouping.OTHER),
    ],
)
def test_categorize(section, analyte, expected) -> None:
    assert grouping.categorize(section, analyte) == expected


def test_imaging_is_not_produced_by_categorize() -> None:
    # Imaging is set by the caller (narrative reports), never inferred from an analyte row.
    assert grouping.categorize("МРТ", "опис") != grouping.IMAGING
