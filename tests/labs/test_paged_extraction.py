"""Paged extraction: split a PDF into chunks, read them concurrently, merge (no subprocess)."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from pathlib import Path

from pypdf import PdfWriter

from dbaylo.labs import extraction
from dbaylo.labs.extraction import (
    ExtractionFailed,
    extract_document,
    extract_paged,
    merge_reports,
)
from dbaylo.labs.pdf_split import _chunk_sizes, is_multipage_pdf, page_count, split_into_chunks
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.llm import ClaudeResult


def _make_pdf(directory: Path, *, pages: int, name: str = "report.pdf") -> str:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    file = directory / name
    with file.open("wb") as handle:
        writer.write(handle)
    return str(file)


def _chunk_runner(per_chunk: dict[int, str]):
    """A fake runner returning chunk-specific JSON based on the chunk file in the prompt."""

    async def run(prompt: str, **kwargs: object) -> ClaudeResult:
        match = re.search(r"-c(\d+)\.pdf", prompt)
        chunk = int(match.group(1)) if match else 1
        text = per_chunk.get(chunk, "")
        ok = bool(text)
        return ClaudeResult(ok=ok, text=text, raw_stdout=text, exit_code=0 if ok else 1)

    return run


def _rows_json(*names: str) -> str:
    return json.dumps({"results": [{"analyte": n, "value": 1.0} for n in names]})


# --- pdf_split ------------------------------------------------------------------


def test_chunk_sizes_are_contiguous_and_even() -> None:
    assert _chunk_sizes(8, 2) == [4, 4]
    assert _chunk_sizes(8, 3) == [3, 3, 2]
    assert _chunk_sizes(2, 5) == [1, 1]  # never more chunks than pages
    assert _chunk_sizes(1, 2) == [1]


def test_is_multipage_pdf_detects_only_multipage_pdfs(tmp_path: Path) -> None:
    assert is_multipage_pdf(_make_pdf(tmp_path, pages=3))
    assert not is_multipage_pdf(_make_pdf(tmp_path, pages=1, name="one.pdf"))
    assert not is_multipage_pdf(tmp_path / "scan.jpg")  # an image is never paged


def test_split_into_chunks_distributes_pages_and_cleans_up(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path, pages=8)
    leaked: list[Path] = []
    with split_into_chunks(pdf, 2) as chunks:
        assert len(chunks) == 2
        assert [page_count(c) for c in chunks] == [4, 4]  # contiguous halves
        leaked = list(chunks)
    assert not any(c.exists() for c in leaked)  # temp dir removed on exit
    assert Path(pdf).exists()  # original untouched


# --- merge_reports (pure) -------------------------------------------------------


def test_merge_concats_rows_and_takes_first_metadata() -> None:
    p1 = ExtractedReport(
        results=[ExtractedAnalyte("Глюкоза", value=5.0)], report_date=date(2026, 6, 1), lab="Синево"
    )
    p2 = ExtractedReport(
        results=[ExtractedAnalyte("Гемоглобін", value=140.0)], lab="Синево, Львів", conclusion="OK"
    )
    merged = merge_reports([p1, p2])
    assert [a.analyte for a in merged.results] == ["Глюкоза", "Гемоглобін"]
    assert merged.report_date == date(2026, 6, 1)  # first non-null
    # most complete lab name across chunks wins, then canonicalized ("Синево" -> "Сінево")
    assert merged.lab == "Сінево, Львів"
    assert merged.conclusion == "OK"  # picked up from the chunk that printed it


def test_merge_keeps_the_brand_over_a_bare_facility_line() -> None:
    chunks = [
        ExtractedReport(results=[ExtractedAnalyte("A", value=1.0)], lab="Лабораторія Львів"),
        ExtractedReport(results=[ExtractedAnalyte("B", value=2.0)], lab="Синево (Synevo), Львів"),
    ]
    assert merge_reports(chunks).lab == "Синево (Synevo), Львів"


def test_merge_drops_exact_duplicate_rows() -> None:
    dup = ExtractedAnalyte("Креатинін", value=90.0, unit="мкмоль/л")
    merged = merge_reports(
        [
            ExtractedReport(results=[dup]),
            ExtractedReport(results=[ExtractedAnalyte("Креатинін", value=90.0, unit="мкмоль/л")]),
        ]
    )
    assert len(merged.results) == 1


def test_merge_concatenates_narratives() -> None:
    merged = merge_reports(
        [ExtractedReport(narrative="Перша сторінка."), ExtractedReport(narrative="Друга.")]
    )
    assert merged.narrative == "Перша сторінка.\n\nДруга."
    assert merged.is_narrative


# --- extract_paged --------------------------------------------------------------


async def test_extract_paged_merges_chunks(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path, pages=8)
    runner = _chunk_runner({1: _rows_json("A", "B"), 2: _rows_json("C", "D")})
    out = await extract_paged(pdf, runner=runner, concurrency=2)
    assert isinstance(out, ExtractedReport)
    assert {a.analyte for a in out.results} == {"A", "B", "C", "D"}


async def test_extract_paged_makes_one_call_per_chunk(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path, pages=8)
    calls = 0

    async def run(prompt: str, **kwargs: object) -> ClaudeResult:
        nonlocal calls
        calls += 1
        text = _rows_json(f"A{calls}")
        return ClaudeResult(ok=True, text=text, raw_stdout=text, exit_code=0)

    await extract_paged(pdf, runner=run, concurrency=2)
    assert calls == 2  # two chunks => two claude calls, not one-per-page


async def test_extract_paged_tolerates_a_failed_chunk(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path, pages=8)
    runner = _chunk_runner({1: _rows_json("A"), 2: ""})  # second chunk unreadable
    out = await extract_paged(pdf, runner=runner, concurrency=2)
    assert isinstance(out, ExtractedReport)
    assert {a.analyte for a in out.results} == {"A"}  # the good chunk survives


async def test_extract_paged_all_chunks_fail_is_a_failure(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path, pages=4)
    out = await extract_paged(pdf, runner=_chunk_runner({}), concurrency=2)
    assert isinstance(out, ExtractionFailed)


async def test_extract_paged_runs_chunks_concurrently(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path, pages=8)
    active = 0
    max_active = 0

    async def run(prompt: str, **kwargs: object) -> ClaudeResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        text = _rows_json("X")
        return ClaudeResult(ok=True, text=text, raw_stdout=text, exit_code=0)

    await extract_paged(pdf, runner=run, concurrency=2)
    assert max_active == 2  # both chunks were in flight at once


# --- extract_document routing ---------------------------------------------------


async def test_extract_document_pages_a_multipage_pdf(tmp_path: Path, monkeypatch) -> None:
    seen = {}

    async def fake_paged(file_path, **kwargs):
        seen["paged"] = file_path
        return ExtractedReport(results=[ExtractedAnalyte("X", value=1.0)])

    monkeypatch.setattr(extraction, "extract_paged", fake_paged)
    pdf = _make_pdf(tmp_path, pages=3)
    await extract_document(pdf)
    assert seen.get("paged") == pdf


async def test_extract_document_single_pass_for_one_page(tmp_path: Path, monkeypatch) -> None:
    seen = {}

    async def fake_single(file_path, **kwargs):
        seen["single"] = file_path
        return ExtractedReport(results=[ExtractedAnalyte("X", value=1.0)])

    monkeypatch.setattr(extraction, "extract_with_escalation", fake_single)
    pdf = _make_pdf(tmp_path, pages=1)
    await extract_document(pdf)
    assert seen.get("single") == pdf
