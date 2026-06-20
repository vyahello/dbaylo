"""Split a PDF into a few contiguous page-range chunks for concurrent extraction.

A big multi-page panel is faster to read as a few parallel chunks than in one serial vision
pass. Crucially we split into **as many chunks as we can run at once** (the concurrency budget),
NOT one-per-page: each ``claude`` invocation carries a large fixed start-up cost, so a handful of
multi-page calls amortises that cost far better than dozens of tiny ones while still parallelising
the (server-side) model work. This module only paginates — it never reads content — and never
modifies the original (rail #5); the chunk files live in a caller-managed temp directory.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader, PdfWriter


def page_count(file_path: str | Path) -> int:
    """Number of pages in a PDF; 0 if it cannot be read as one (caller falls back)."""
    try:
        return len(PdfReader(str(file_path)).pages)
    except Exception:  # noqa: BLE001 — a malformed/non-PDF just means "don't page it"
        return 0


def is_multipage_pdf(file_path: str | Path) -> bool:
    """True only for a PDF with more than one page (the case paging helps)."""
    return Path(file_path).suffix.lower() == ".pdf" and page_count(file_path) > 1


def _chunk_sizes(total: int, n_chunks: int) -> list[int]:
    """Split ``total`` pages into ``n_chunks`` contiguous, as-even-as-possible ranges."""
    n = max(1, min(n_chunks, total))
    base, extra = divmod(total, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


@contextlib.contextmanager
def split_into_chunks(file_path: str | Path, n_chunks: int) -> Iterator[list[Path]]:
    """Yield up to ``n_chunks`` single-PDF files, each a contiguous slice of pages, in order.

    The temp directory (and every chunk file) is removed when the context exits, so callers
    must finish extracting before leaving the ``with`` block. The source file is untouched.
    """
    source = Path(file_path)
    reader = PdfReader(str(source))
    sizes = _chunk_sizes(len(reader.pages), n_chunks)
    with TemporaryDirectory(prefix="dbaylo-pages-") as tmp:
        tmp_dir = Path(tmp)
        chunks: list[Path] = []
        start = 0
        for index, size in enumerate(sizes):
            writer = PdfWriter()
            for page in range(start, start + size):
                writer.add_page(reader.pages[page])
            out = tmp_dir / f"{source.stem}-c{index + 1:02d}.pdf"
            with out.open("wb") as handle:
                writer.write(handle)
            chunks.append(out)
            start += size
        yield chunks
