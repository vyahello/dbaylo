"""The deterministic core of the generic-section backfill (report-context specimen tagging)."""

from __future__ import annotations

from dbaylo.labs.trends import series_key, specimen
from dbaylo.maintenance.backfill_sections import RowView, plan_report, report_specimen


def _rows(*triples: tuple[int, str, str | None]) -> list[RowView]:
    return [RowView(i, analyte, section) for i, analyte, section in triples]


def test_report_specimen_from_unambiguous_sections() -> None:
    semen = _rows((1, "Об'єм", "Спермограма"), (2, "Активнорухливих (%)", "Кінезисграма"))
    urine = _rows((1, "pH", "Загальний аналіз сечі"), (2, "Колір", "Фізико-хімічні властивості"))
    mixed = _rows((1, "ПСА", "Спермограма"), (2, "Лейкоцити", "Загальний аналіз сечі"))
    blood = _rows((1, "Гемоглобін", "Загальний аналіз крові"))
    assert report_specimen(semen) == "semen"
    assert report_specimen(urine) == "urine"
    assert report_specimen(mixed) is None  # both fluids -> ambiguous -> leave it
    assert report_specimen(blood) is None  # blood-only -> nothing to re-tag


def test_plan_retags_generic_sections_in_a_semen_report() -> None:
    rows = _rows(
        (10, "Кількість сперматозоїдів в еякуляті", "Мікроскопічне дослідження"),  # already semen
        (11, "Еритроцити", "Мікроскопічне дослідження"),  # generic -> blood -> re-tag to semen
        (12, "Активнорухливих (%)", "Кінезисграма"),  # generic -> blood -> re-tag
        (13, "Тестостерон", "Спермограма"),  # already semen by section -> untouched
    )
    plan = dict(plan_report(rows))
    assert 10 not in plan  # a sperm-count row already keys to semen via its name
    assert plan[11] == "Спермограма: Мікроскопічне дослідження"
    assert plan[12] == "Спермограма: Кінезисграма"
    assert 13 not in plan
    # The re-tagged section now classifies as semen, not blood.
    assert specimen(plan[11], "Еритроцити") == "semen"


def test_plan_retags_urine_chemistry_in_a_urine_report() -> None:
    rows = _rows(
        (20, "Фізико-хімічні властивості: Глюкоза", "Фізико-хімічні властивості"),
        (21, "Лейкоцити", "Мікроскопія осаду сечі"),  # already urine -> untouched
    )
    plan = dict(plan_report(rows))
    assert plan[20] == "Аналіз сечі: Фізико-хімічні властивості"
    assert 21 not in plan
    # urine glucose now keys to a urine series, separate from blood "Глюкоза (сироватка)".
    assert series_key(plan[20], "Глюкоза") != series_key("Біохімічний аналіз крові", "Глюкоза")


def test_plan_leaves_blood_tests_that_merely_share_a_urine_report() -> None:
    # A ПСА / hormone panel in a report that also has a urine panel must NOT be re-tagged: its
    # section is not generic, so it is never a candidate.
    rows = _rows(
        (30, "Простат-специфічний антиген загальний (ПСА)", "Пакет №7 (ПСА: ...)"),
        (31, "Лейкоцити", "Загальний аналіз сечі"),
    )
    assert plan_report(rows) == []  # ПСА section isn't generic -> untouched


def test_plan_is_idempotent() -> None:
    rows = _rows((40, "Еритроцити", "Спермограма: Мікроскопічне дослідження"))
    assert plan_report(rows) == []  # already prefixed -> not generic, classifies as semen
