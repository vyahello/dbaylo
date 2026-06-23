"""Extraction tests: the defensive parser and the call path (no subprocess)."""

from __future__ import annotations

from datetime import date

import pytest

from dbaylo.labs.extraction import (
    ExtractedReport,
    ExtractionFailed,
    extract,
    extract_with_escalation,
    parse_extraction,
)
from dbaylo.llm import ClaudeResult, ClaudeUnavailable

GOOD_JSON = """
{"report_date": "2026-05-12", "lab": "Synevo", "results": [
  {"analyte": "Глюкоза", "value": 5.4, "unit": "ммоль/л", "ref_low": 3.9, "ref_high": 6.1},
  {"analyte": "Сечовина", "value": "9,1", "unit": "ммоль/л", "ref_low": 2.5, "ref_high": 8.3}
]}
"""


def _runner(text: str, ok: bool = True):
    async def run(*args, **kwargs) -> ClaudeResult:
        return ClaudeResult(ok=ok, text=text, raw_stdout=text, exit_code=0 if ok else 1)

    return run


# --- parse_extraction -----------------------------------------------------------


def test_parse_good_json() -> None:
    report = parse_extraction(GOOD_JSON)
    assert report is not None
    assert report.report_date == date(2026, 5, 12)
    assert report.lab == "Сінево"  # "Synevo" canonicalized to the printed Ukrainian brand
    assert len(report.results) == 2
    assert report.results[1].value == pytest.approx(9.1)  # comma decimal coerced


def test_parse_strips_code_fences() -> None:
    fenced = "```json\n" + GOOD_JSON.strip() + "\n```"
    report = parse_extraction(fenced)
    assert report is not None and len(report.results) == 2


def test_parse_brace_substring_fallback() -> None:
    noisy = "Ось результати:\n" + GOOD_JSON.strip() + "\nГотово."
    report = parse_extraction(noisy)
    assert report is not None and report.lab == "Сінево"


def test_parse_qualitative_value_goes_to_text() -> None:
    report = parse_extraction(
        '{"results": [{"analyte": "Кетони", "value": null, "value_text": "не виявлено"}]}'
    )
    assert report is not None
    assert report.results[0].value is None
    assert report.results[0].value_text == "не виявлено"


def test_parse_derives_numeric_bounds_from_ref_text() -> None:
    # A one-sided range the model left as free text -> numeric bound recovered (so a chart can
    # draw the band). The verbatim ref_text is preserved.
    report = parse_extraction(
        '{"results": [{"analyte": "Холестерин", "value": 5.85,'
        ' "ref_low": null, "ref_high": null, "ref_text": "< 5.2"}]}'
    )
    assert report is not None
    row = report.results[0]
    assert row.ref_high == 5.2 and row.ref_low is None and row.ref_text == "< 5.2"


def test_parse_keeps_explicit_numeric_refs_over_ref_text() -> None:
    report = parse_extraction(
        '{"results": [{"analyte": "Глюкоза", "value": 5.0,'
        ' "ref_low": 3.9, "ref_high": 6.1, "ref_text": "3.9-6.1"}]}'
    )
    assert report is not None
    assert report.results[0].ref_low == 3.9 and report.results[0].ref_high == 6.1


def test_parse_captures_birth_date_and_sex() -> None:
    report = parse_extraction(
        '{"report_date": "2023-04-18", "birth_date": "1993-03-24", "sex": "Чоловіча",'
        ' "results": [{"analyte": "ПСА", "value": 0.58}]}'
    )
    assert report is not None
    assert report.birth_date == date(1993, 3, 24) and report.sex == "m"


def test_parse_does_not_flatten_an_age_table_ref_into_a_band() -> None:
    # An age-stratified ref must stay as free text (ref_low/high None) so it is resolved by age at
    # read time — NOT mis-parsed into the band (40, 50) by grabbing the age span "40-50".
    report = parse_extraction(
        '{"results": [{"analyte": "ПСА", "value": 0.58, "ref_text":'
        ' "<40 років: <1.4; 40-50 років: <2.0; 50-60 років: <3.1"}]}'
    )
    assert report is not None
    row = report.results[0]
    assert row.ref_low is None and row.ref_high is None
    assert row.ref_text and "40-50" in row.ref_text  # verbatim table kept for age resolution


def test_parse_conclusion_and_out_of_range() -> None:
    report = parse_extraction(
        '{"conclusion": "Нормозооспермія", "results": ['
        '{"analyte": "Лейкоцити", "value_text": "10-15", "out_of_range": true},'
        '{"analyte": "Об\'єм", "value": 2.0, "ref_low": 1.5, "out_of_range": false}]}'
    )
    assert report is not None
    assert report.conclusion == "Нормозооспермія"
    assert report.results[0].out_of_range is True  # lab flagged
    assert report.results[1].out_of_range is False


def test_parse_out_of_range_tolerates_string_bool() -> None:
    report = parse_extraction(
        '{"results": [{"analyte": "X", "value": 1.0, "out_of_range": "true"}]}'
    )
    assert report is not None and report.results[0].out_of_range is True


def test_parse_narrative_document() -> None:
    report = parse_extraction(
        '{"kind": "narrative", "report_type": "МРТ головного мозку",'
        ' "narrative": "Без вогнищевих змін.", "conclusion": "Патології не виявлено",'
        ' "results": []}'
    )
    assert report is not None
    assert report.is_narrative and report.is_usable
    assert report.report_type == "МРТ головного мозку"
    assert report.narrative == "Без вогнищевих змін."
    assert report.conclusion == "Патології не виявлено"


def test_parse_narrative_tolerates_missing_results() -> None:
    report = parse_extraction('{"report_type": "УЗД", "narrative": "опис"}')
    assert report is not None and report.is_narrative


def test_parse_narrative_wins_over_stray_rows() -> None:
    # The МРТ bug: the model captured the findings narrative but ALSO emitted stray "rows" from
    # the patient/device info (no value/ref). The narrative must win, not be discarded.
    report = parse_extraction(
        '{"kind": "narrative", "report_type": "МРТ головного мозку",'
        ' "narrative": "Без вогнищевих змін.", "conclusion": "Без патології",'
        ' "results": [{"analyte": "Пацієнт", "value": null},'
        ' {"analyte": "Пристрій", "value": null}]}'
    )
    assert report is not None
    assert report.is_narrative and not report.results  # stray rows dropped
    assert report.narrative == "Без вогнищевих змін."


def test_parse_narrative_inferred_when_kind_missing_and_no_real_rows() -> None:
    # Even without an explicit "kind", a report_type/narrative + only stray rows -> narrative.
    report = parse_extraction(
        '{"report_type": "КТ органів грудної клітки", "narrative": "Норма.",'
        ' "results": [{"analyte": "Дата", "value": null}]}'
    )
    assert report is not None and report.is_narrative and not report.results


def test_parse_real_table_stays_tabular_even_with_a_stray_narrative() -> None:
    # A genuine analyte table (rows WITH measured values) must stay tabular, keeping its rows,
    # even if the model also filled a narrative field.
    report = parse_extraction(
        '{"kind": "tabular", "narrative": "побічна нотатка",'
        ' "results": [{"analyte": "Глюкоза", "value": 5.4, "ref_low": 3.9, "ref_high": 6.1}]}'
    )
    assert report is not None
    assert not report.is_narrative and len(report.results) == 1  # rows kept, narrative dropped
    assert report.narrative is None


async def test_extract_succeeds_on_narrative(tmp_path) -> None:
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    narrative = '{"kind": "narrative", "report_type": "УЗД", "narrative": "опис", "results": []}'
    outcome = await extract(f, runner=_runner(narrative))
    assert isinstance(outcome, ExtractedReport) and outcome.is_narrative


def test_parse_nonnumeric_value_demoted_to_text() -> None:
    report = parse_extraction('{"results": [{"analyte": "Колір", "value": "солом’яний"}]}')
    assert report is not None
    assert report.results[0].value is None
    assert report.results[0].value_text == "солом’яний"


def test_parse_skips_rows_without_analyte() -> None:
    report = parse_extraction(
        '{"results": ['
        '{"value": 5}, {"analyte": "  ", "value": 6}, {"analyte": "Калій", "value": 4.2}'
        "]}"
    )
    assert report is not None
    assert [r.analyte for r in report.results] == ["Калій"]


def test_parse_bad_date_becomes_none() -> None:
    report = parse_extraction(
        '{"report_date": "12.05.2026", "results": [{"analyte": "X", "value": 1}]}'
    )
    assert report is not None and report.report_date is None


@pytest.mark.parametrize("text", ["", "   ", "not json at all", "{}", '{"results": "nope"}', "[]"])
def test_parse_unrecoverable_returns_none_or_empty(text: str) -> None:
    report = parse_extraction(text)
    assert report is None or report.results == []


# --- extract() ------------------------------------------------------------------


async def test_extract_success(tmp_path) -> None:
    f = tmp_path / "lab.png"
    f.write_bytes(b"x")
    outcome = await extract(f, runner=_runner(GOOD_JSON))
    assert isinstance(outcome, ExtractedReport)
    assert len(outcome.results) == 2


async def test_extract_missing_file() -> None:
    outcome = await extract("/no/such/file.png", runner=_runner(GOOD_JSON))
    assert isinstance(outcome, ExtractionFailed)


async def test_extract_call_not_ok(tmp_path) -> None:
    f = tmp_path / "lab.png"
    f.write_bytes(b"x")
    outcome = await extract(f, runner=_runner("", ok=False))
    assert isinstance(outcome, ExtractionFailed)


async def test_extract_unparseable_text(tmp_path) -> None:
    f = tmp_path / "lab.png"
    f.write_bytes(b"x")
    outcome = await extract(f, runner=_runner("garbage, no json"))
    assert isinstance(outcome, ExtractionFailed)


async def test_extract_claude_unavailable(tmp_path) -> None:
    f = tmp_path / "lab.png"
    f.write_bytes(b"x")

    async def boom(*args, **kwargs):
        raise ClaudeUnavailable("missing binary")

    outcome = await extract(f, runner=boom)
    assert isinstance(outcome, ExtractionFailed)
    assert "claude unavailable" in outcome.reason


async def test_escalation_falls_through_to_second_model(tmp_path) -> None:
    f = tmp_path / "lab.png"
    f.write_bytes(b"x")
    calls: list[str | None] = []

    async def run(*args, model=None, **kwargs) -> ClaudeResult:
        calls.append(model)
        text = GOOD_JSON if model == "opus" else "garbage"
        return ClaudeResult(ok=True, text=text, raw_stdout=text, exit_code=0)

    outcome = await extract_with_escalation(f, models=("sonnet", "opus"), runner=run)
    assert isinstance(outcome, ExtractedReport)
    assert calls == ["sonnet", "opus"]


async def test_escalation_stops_on_timeout(tmp_path) -> None:
    f = tmp_path / "lab.png"
    f.write_bytes(b"x")
    calls: list[str | None] = []

    async def run(*args, model=None, **kwargs) -> ClaudeResult:
        calls.append(model)
        return ClaudeResult(ok=False, text="", raw_stdout="", exit_code=None, error="timeout")

    outcome = await extract_with_escalation(f, models=("sonnet", "opus"), runner=run)
    assert isinstance(outcome, ExtractionFailed)
    assert calls == ["sonnet"]  # a timeout fails fast; the slower opus is not tried
