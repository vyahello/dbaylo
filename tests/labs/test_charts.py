"""Chart rendering smoke tests (deterministic, headless)."""

from __future__ import annotations

from datetime import date

import pytest

from dbaylo.labs.charts import (
    PdfChart,
    PdfCover,
    PdfQualTrend,
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


def test_readable_ticks_never_crowds_the_final_date() -> None:
    # The reported bug: the two most recent measurements were ~a month apart while the rest spanned
    # years, so the last two x-axis labels overlapped into an unreadable smear ("2023-04-37"). The
    # final date is always shown, but a neighbour that would collide with it must be dropped.
    values = [0.0, 130.0, 260.0, 390.0, 520.0, 650.0, 820.0, 850.0]  # last two only 30 apart
    ticks = _readable_ticks(values, max_ticks=7)
    assert ticks[-1] == 850.0  # the latest date is kept
    assert 820.0 not in ticks  # its crowding neighbour is dropped — no overlap
    min_gap = (values[-1] - values[0]) / 7
    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    assert all(g >= min_gap for g in gaps)  # every adjacent label pair is readably spaced


def test_pdf_text_strips_emoji_keeps_punctuation() -> None:
    # matplotlib's PDF font has no emoji glyphs; strip them but keep the dash, middle dot, bullet.
    assert _pdf_text("📈 6 — норма · вимірів: 2") == "6 — норма · вимірів: 2"
    assert _pdf_text("⚠️ Лейкоцити") == "Лейкоцити"
    assert _pdf_text("• pH") == "• pH"


def test_pdf_wrap_keeps_text_inside_the_card() -> None:
    from dbaylo.labs.charts import _clip, _wrap

    # A long note must hard-wrap (matplotlib's own wrap ran off the page edge before).
    note = "Дріжджеподібні гриби в осаді сечі — це сигнал можливого грибкового запалення."
    wrapped = _wrap(note * 2, width=40)
    assert "\n" in wrapped and all(len(line) <= 44 for line in wrapped.split("\n"))
    assert _wrap("перший\n\nдругий") == "перший\n\nдругий"  # blank line between paragraphs kept
    # A long header title is clipped to one line.
    assert _clip("x" * 60).endswith("…") and len(_clip("x" * 60)) <= 42
    assert _clip("Лейкоцити") == "Лейкоцити"


def test_render_trends_pdf_is_a_valid_multipage_pdf() -> None:
    pts = [
        LabPoint("pH", date(2026, 1, 1), 5.0, "", 5.0, 7.0),
        LabPoint("pH", date(2026, 2, 1), 6.0, "", 5.0, 7.0),
    ]
    pages = [
        PdfChart(
            title="📈 pH (сеча)",
            subtitle="🔬 Сеча",
            points=pts,
            caption="📈 6 — тримається в межах норми",
        ),
        PdfChart(
            title="Лейкоцити",
            subtitle="🩸 Кров",
            points=pts,
            caption="📈 6 — норма\n\nЛейкоцити — клітини.",
        ),
    ]
    cover = PdfCover(
        heading="Динаміка показників",
        report_line="За аналізом від 2026-02-01 · Сінево",
        summary_line="На графіках — 2 показників із числовою динамікою",
        category_rows=("Сеча — 1", "Кров — 1"),
        notes=("Ще 3 якісних показників — словами наприкінці.", "Усього у звіті: 5 показників."),
    )
    pdf = render_trends_pdf(pages, cover=cover)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 1000


def test_render_trends_pdf_includes_qualitative_timeline_section() -> None:
    cover = PdfCover(heading="Динаміка показників", report_line="За аналізом від 2026-02-01 · ДІЛА")
    quals = (
        PdfQualTrend(
            title="Бактерії (сеча)",
            subtitle="🔬 Сеча",
            rows=(("2023-04-05", "не виявлені", False), ("2026-02-01", "виявлено", True)),
            note="Бактерії в сечі — можливий запальний процес.",
            changed=True,
        ),
    )
    # A PDF that has NO numeric charts but DOES have a qualitative timeline still renders (the
    # qualitative indicators must not be silently dropped).
    pdf = render_trends_pdf([], cover=cover, qual_trends=quals)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 1000


def test_render_highlights_the_source_report_point() -> None:
    # When opened from a report, that report's measurement is ringed — still a valid PNG, and the
    # highlight_date that matches no point degrades gracefully (no crash, still a chart).
    pts = [_pt(1, 5.0, 3.0, 7.0), _pt(15, 6.0, 3.0, 7.0)]
    png = render_trend_chart(pts, title="Глюкоза", highlight_date=date(2026, 1, 1))
    assert png[:8] == _PNG_MAGIC
    missing = render_trend_chart(pts, title="Глюкоза", highlight_date=date(1999, 1, 1))
    assert missing[:8] == _PNG_MAGIC


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


def _chart(name, vals, lo, hi):
    pts = [LabPoint(name, date(2023, m, 1), v, "од", lo, hi) for m, v in vals]
    return PdfChart(title=name, subtitle="", points=pts, caption="")


def test_latest_out_uses_the_last_measurement() -> None:
    from dbaylo.labs.charts import _latest_out

    # Latest 7.1 (>6.1) -> out; latest back in range -> not out.
    assert _latest_out(_chart("Глюкоза", [(1, 5.0), (6, 7.1)], 3.9, 6.1).points) is True
    assert _latest_out(_chart("Глюкоза", [(1, 7.1), (6, 5.0)], 3.9, 6.1).points) is False


def test_overview_page_emitted_for_many_charts_flagged_first() -> None:
    from dbaylo.labs import charts as charts_mod

    captured: list[object] = []

    class _Shim:
        def savefig(self, fig):
            captured.append(fig)

    ok = _chart("Холестерин", [(1, 4.9), (6, 5.0)], None, 5.2)  # in range
    bad = _chart("Глюкоза", [(1, 5.0), (6, 7.1)], 3.9, 6.1)  # out of range
    charts_mod._overview_pages(_Shim(), [ok, bad], heading="Огляд")
    assert len(captured) == 1  # one grid page for two numeric charts

    # 0–1 charts need no overview page.
    captured.clear()
    charts_mod._overview_pages(_Shim(), [bad], heading="Огляд")
    assert captured == []


def test_render_trends_pdf_includes_the_overview_page() -> None:
    charts = [
        _chart("Глюкоза", [(1, 5.0), (6, 7.1)], 3.9, 6.1),
        _chart("АЛТ", [(1, 22), (6, 55)], None, 40),
    ]
    cover = PdfCover(heading="Динаміка", report_line="", stat_tiles=(("Поза нормою", "2"),))
    pdf = render_trends_pdf(charts, cover=cover)
    assert pdf[:4] == b"%PDF"  # a valid PDF is produced (cover + overview + 2 chart pages)
