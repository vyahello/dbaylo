"""Prescription extraction: defensive parsing + a fake-runner extraction pass.

Like the lab extractor, the model output is parsed tolerantly (fences, partial, non-prescription)
and never raises — a bad response degrades to ExtractionFailed.
"""

from __future__ import annotations

from pathlib import Path

from dbaylo.labs.extraction import ExtractionFailed
from dbaylo.labs.prescription import (
    ExtractedMedication,
    extract_prescription,
    parse_prescription,
)
from dbaylo.llm import ClaudeResult


def test_parse_reads_name_dose_times_duration_and_course() -> None:
    text = (
        '{"course": "Урологічний курс", "medications": [{"name": "Аспірин", "dose": "500 мг", '
        '"times": ["8:00", "20:00"], "frequency": null, "duration": "3 міс."}]}'
    )
    rx = parse_prescription(text)
    assert rx is not None and rx.course == "Урологічний курс"  # the model's clinical naming
    assert rx.medications == [
        ExtractedMedication(
            name="Аспірин",
            dose="500 мг",
            times=("08:00", "20:00"),  # "8:00" padded to "08:00"
            frequency=None,
            duration="3 міс.",
        )
    ]


def test_parse_tolerates_code_fences() -> None:
    text = '```json\n{"medications": [{"name": "Парацетамол", "times": []}]}\n```'
    rx = parse_prescription(text)
    assert (
        rx is not None and rx.medications[0].name == "Парацетамол" and rx.medications[0].times == ()
    )


def test_parse_drops_invalid_times_and_keeps_frequency() -> None:
    text = (
        '{"medications": [{"name": "Сироп", "dose": "10 мл", '
        '"times": ["25:00", "noon"], "frequency": "двічі на день"}]}'
    )
    rx = parse_prescription(text)
    assert rx is not None
    assert rx.medications[0].times == ()  # "25:00"/"noon" are not valid HH:MM
    assert rx.medications[0].frequency == "двічі на день"


def test_parse_empty_medications_is_valid() -> None:
    rx = parse_prescription('{"medications": []}')  # not a prescription -> empty meds, not None
    assert rx is not None and rx.medications == []


def test_parse_garbage_returns_none() -> None:
    assert parse_prescription("totally not json") is None
    assert parse_prescription("") is None


async def test_extract_uses_runner_and_parses(tmp_path: Path) -> None:
    file = tmp_path / "rx.jpg"
    file.write_bytes(b"fake")

    async def runner(prompt, **kwargs) -> ClaudeResult:
        return ClaudeResult(
            ok=True,
            text='{"medications": [{"name": "Метформін", "dose": "850 мг", "times": ["09:00"]}]}',
            raw_stdout="",
            exit_code=0,
        )

    rx = await extract_prescription(file, runner=runner)
    assert not isinstance(rx, ExtractionFailed)
    assert rx.medications[0].name == "Метформін" and rx.medications[0].dose == "850 мг"


async def test_extract_missing_file_fails_cleanly() -> None:
    async def runner(*a, **k):  # never called
        raise AssertionError("runner must not run for a missing file")

    outcome = await extract_prescription("/no/such/file.jpg", runner=runner)
    assert isinstance(outcome, ExtractionFailed)


async def test_extract_failed_claude_call_degrades(tmp_path: Path) -> None:
    file = tmp_path / "rx.jpg"
    file.write_bytes(b"x")

    async def runner(prompt, **kwargs) -> ClaudeResult:
        return ClaudeResult(ok=False, text="", raw_stdout="", exit_code=1, error="boom")

    outcome = await extract_prescription(file, runner=runner)
    assert isinstance(outcome, ExtractionFailed)
