"""Chart rendering smoke tests (deterministic, headless)."""

from __future__ import annotations

from datetime import date

from dbaylo.labs.charts import render_trend_chart
from dbaylo.labs.trends import LabPoint

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _pt(day: int, value: float, low=None, high=None):
    return LabPoint("Глюкоза", date(2026, 1, day), value, "ммоль/л", low, high)


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
