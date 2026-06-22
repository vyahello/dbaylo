"""Chart rendering smoke tests (deterministic, headless)."""

from __future__ import annotations

from datetime import date

import pytest

from dbaylo.labs.charts import (
    PdfChart,
    _out_of_range,
    _pdf_text,
    _readable_ticks,
    render_trend_chart,
    render_trends_pdf,
)
from dbaylo.labs.trends import LabPoint

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _pt(day: int, value: float, low=None, high=None):
    return LabPoint("Глюкоза", date(2026, 1, day), value, "ммоль/л", low, high)


def test_readable_ticks_keeps_few_dates_and_thins_clusters() -> None:
    # A handful of dates are all kept (every one is a real measurement).
    assert _readable_ticks([1.0, 5.0, 9.0]) == [1.0, 5.0, 9.0]
    # A long run on consecutive days is thinned, but always keeps the first and last and every
    # kept tick is one of the real input dates (never an interpolated one).
    days = [float(d) for d in range(1, 31)]  # 30 daily samples
    ticks = _readable_ticks(days, max_ticks=7)
    assert ticks[0] == 1.0 and ticks[-1] == 30.0
    assert len(ticks) <= 8  # not one-per-day
    assert all(t in days for t in ticks)


def test_readable_ticks_thins_a_time_cluster_between_two_far_dates() -> None:
    # The real bug: one early date, a dense 2023 cluster, one late date — the cluster must not
    # contribute many overlapping labels.
    cluster = [100.0 + i * 0.5 for i in range(12)]  # 12 dates within ~6 days
    values = [0.0, *cluster, 330.0]
    ticks = _readable_ticks(values, max_ticks=7)
    assert ticks[0] == 0.0 and ticks[-1] == 330.0
    in_cluster = [t for t in ticks if 100.0 <= t <= 106.0]
    assert len(in_cluster) <= 2  # the smear is gone


def test_pdf_text_strips_emoji_keeps_punctuation() -> None:
    # matplotlib's PDF font has no emoji glyphs; strip them but keep the dash, middle dot, bullet.
    assert _pdf_text("📈 6 — норма · вимірів: 2") == "6 — норма · вимірів: 2"
    assert _pdf_text("⚠️ Лейкоцити") == "Лейкоцити"
    assert _pdf_text("• pH") == "• pH"


def test_render_trends_pdf_is_a_valid_multipage_pdf() -> None:
    pts = [
        LabPoint("pH", date(2026, 1, 1), 5.0, "", 5.0, 7.0),
        LabPoint("pH", date(2026, 2, 1), 6.0, "", 5.0, 7.0),
    ]
    pages = [
        PdfChart(title="📈 pH (сеча)", points=pts, caption="📈 6 — тримається в межах норми"),
        PdfChart(title="Лейкоцити", points=pts, caption="📈 6 — норма\n\nЛейкоцити — клітини."),
    ]
    pdf = render_trends_pdf(pages, heading="Динаміка показників")
    assert pdf[:5] == b"%PDF-" and len(pdf) > 1000


def test_render_returns_png_with_reference_band() -> None:
    png = render_trend_chart([_pt(1, 7.0, 3.9, 6.1), _pt(20, 5.4, 3.9, 6.1)], title="Глюкоза")
    assert png.startswith(_PNG_MAGIC)
    assert len(png) > 1000


def test_render_single_point() -> None:
    png = render_trend_chart([_pt(1, 5.4, 3.9, 6.1)], title="Глюкоза")
    assert png.startswith(_PNG_MAGIC)


def test_render_no_reference_range() -> None:
    png = render_trend_chart([_pt(1, 5.0), _pt(2, 6.0)], title="Без норми")
    assert png.startswith(_PNG_MAGIC)


def test_render_one_sided_upper_bound() -> None:
    png = render_trend_chart([_pt(1, 5.0, None, 6.0), _pt(2, 7.0, None, 6.0)], title="Верхня межа")
    assert png.startswith(_PNG_MAGIC)


def test_render_flagged_point_without_a_numeric_reference() -> None:
    # The lab flagged a measurement but no numeric ref was captured — the chart must still render
    # (the flagged point shows red even with no band), so a flagged ref-less analyte isn't blank.
    pts = [
        LabPoint("X", date(2026, 1, 1), 5.0, "од", None, None, flagged=False),
        LabPoint("X", date(2026, 2, 1), 9.0, "од", None, None, flagged=True),
    ]
    png = render_trend_chart(pts, title="X")
    assert png.startswith(_PNG_MAGIC)


@pytest.mark.parametrize(
    ("value", "lo", "hi", "expected"),
    [
        (5.0, 3.9, 6.1, False),  # inside a two-sided range
        (7.0, 3.9, 6.1, True),  # above the upper bound
        (2.0, 3.9, 6.1, True),  # below the lower bound
        (60.0, None, 50.0, True),  # one-sided upper (≤ 50)
        (40.0, None, 50.0, False),
        (5.0, 4.0, None, False),  # one-sided lower (≥ 4)
        (3.0, 4.0, None, True),
        (5.0, None, None, False),  # no reference -> never "out of range"
    ],
)
def test_out_of_range_classification(value, lo, hi, expected) -> None:
    # This drives the green ●/red ✕ marker colour, so it must be exact.
    assert _out_of_range(value, lo, hi) is expected
