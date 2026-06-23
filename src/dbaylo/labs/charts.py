"""Per-analyte trend charts, rendered headless to PNG bytes.

Deterministic and LLM-free: it draws exactly the numbers it is given. Uses the
matplotlib Agg backend so it works on a headless VPS with no display.

Every chart reads the SAME way (robust across analytes): a green band for the acceptable
range and red band(s) for out of range (drawn consistently whether the range is two-sided,
≤ X, or ≥ X); each measurement is a green ●  if in range or a red ✕ if out of range (shape +
colour, so it survives colour-blindness); out-of-range points are labelled with their value so
the "bottleneck" is obvious. The y-axis always includes the reference bounds, so the band is
never cut off and a flat series is never over-zoomed.
"""

from __future__ import annotations

import io
import re
import textwrap
from dataclasses import dataclass
from datetime import date
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless: must be set before pyplot is imported.

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, Patch, Rectangle  # noqa: E402

from dbaylo import locale  # noqa: E402
from dbaylo.labs.trends import LabPoint  # noqa: E402

_GREEN_ZONE = "#dcfce7"  # acceptable range fill
_RED_ZONE = "#fee2e2"  # out-of-range fill
_BOUND = "#94a3b8"  # the limit lines (ref_low / ref_high)
_LINE = "#64748b"  # the connecting trend line (neutral, so the markers carry the meaning)
_OK = "#16a34a"  # in-range marker
_OUT = "#dc2626"  # out-of-range marker


def _out_of_range(value: float, lo: float | None, hi: float | None) -> bool:
    return (lo is not None and value < lo) or (hi is not None and value > hi)


def _readable_ticks(values: list[float], *, max_ticks: int = 7) -> list[float]:
    """A readable subset of the actual measurement dates for the x-axis. We tick only on real
    dates (never an interpolated 'phantom' one), but when several samples fall close together in
    TIME their labels collide into an unreadable smear — so we keep the first and last and add an
    intermediate date only when it is far enough (in time) from the previously kept one."""
    unique = sorted(set(values))
    if len(unique) <= max_ticks:
        return unique
    min_gap = (unique[-1] - unique[0]) / max_ticks
    kept = [unique[0]]
    for v in unique[1:-1]:
        if v - kept[-1] >= min_gap:
            kept.append(v)
    kept.append(unique[-1])
    return kept


def render_trend_chart(
    points: list[LabPoint], *, title: str, highlight_date: date | None = None
) -> bytes:
    """Render a single analyte's series to PNG bytes (value vs date). When ``highlight_date`` is
    given (the report the chart was opened from), that measurement is ringed and labelled 'цей
    аналіз' so you can see where the report you came from sits in the whole trend."""
    numeric = sorted((p for p in points if p.value is not None), key=lambda p: p.taken_on)

    fig, ax = plt.subplots(figsize=(7, 4), dpi=120)
    try:
        if numeric:
            _draw(ax, fig, numeric, highlight_date=highlight_date)
        ax.set_title(title)
        ax.grid(True, alpha=0.3, zorder=0)

        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", bbox_inches="tight")
        return buffer.getvalue()
    finally:
        plt.close(fig)


def _draw(
    ax: Axes, fig: Figure, numeric: list[LabPoint], *, highlight_date: date | None = None
) -> None:
    xs = [mdates.date2num(p.taken_on) for p in numeric]
    ys = [p.value for p in numeric if p.value is not None]
    # The band: the most RECENT point that actually has a reference (older reports may have captured
    # it even when the latest did not), so a missing latest ref doesn't drop the whole band.
    ref_pt = next(
        (p for p in reversed(numeric) if p.ref_low is not None or p.ref_high is not None),
        numeric[-1],
    )
    lo, hi = ref_pt.ref_low, ref_pt.ref_high

    # Y-limits include the data AND the reference bounds, with padding — so the band is always
    # visible and a flat series (e.g. ШОЕ ≡ 2) is not zoomed to a meaningless sliver.
    span = ys + [b for b in (lo, hi) if b is not None]
    ymin, ymax = min(span), max(span)
    pad = max((ymax - ymin) * 0.15, abs(ymax) * 0.05, 1.0)
    ylo, yhi = ymin - pad, ymax + pad
    ax.set_ylim(ylo, yhi)

    # Zones: green = acceptable, red = out of range — same language for one- or two-sided ranges.
    if lo is not None and hi is not None:
        ax.axhspan(ylo, lo, color=_RED_ZONE, zorder=0)
        ax.axhspan(lo, hi, color=_GREEN_ZONE, zorder=0)
        ax.axhspan(hi, yhi, color=_RED_ZONE, zorder=0)
    elif hi is not None:
        ax.axhspan(ylo, hi, color=_GREEN_ZONE, zorder=0)
        ax.axhspan(hi, yhi, color=_RED_ZONE, zorder=0)
    elif lo is not None:
        ax.axhspan(ylo, lo, color=_RED_ZONE, zorder=0)
        ax.axhspan(lo, yhi, color=_GREEN_ZONE, zorder=0)
    for bound in (lo, hi):
        if bound is not None:
            ax.axhline(bound, color=_BOUND, linestyle="--", linewidth=1, zorder=1)

    # Neutral connecting line; the meaning is carried by the status-coloured markers.
    ax.plot(xs, ys, "-", color=_LINE, linewidth=1.5, zorder=2)
    # A point is out of norm if the LAB flagged it (reliable even with no numeric ref) OR it is
    # numerically outside the band — so a flagged value still shows red even without a band.
    flags = [p.flagged or _out_of_range(y, lo, hi) for p, y in zip(numeric, ys, strict=True)]
    in_x = [x for x, bad in zip(xs, flags, strict=True) if not bad]
    in_y = [y for y, bad in zip(ys, flags, strict=True) if not bad]
    out_x = [x for x, bad in zip(xs, flags, strict=True) if bad]
    out_y = [y for y, bad in zip(ys, flags, strict=True) if bad]
    if in_x:
        ax.scatter(
            in_x, in_y, marker="o", s=55, color=_OK, edgecolors="white", linewidths=0.8, zorder=3
        )
    if out_x:
        ax.scatter(
            out_x, out_y, marker="X", s=90, color=_OUT, edgecolors="white", linewidths=0.8, zorder=4
        )
        for x, y in zip(out_x, out_y, strict=True):  # label the bottleneck points explicitly
            ax.annotate(
                f"{y:g}",
                (x, y),
                textcoords="offset points",
                xytext=(0, 8),
                ha="center",
                fontsize=8,
                color=_OUT,
                fontweight="bold",
            )

    # Ring the measurement of the report this chart was opened from, so its place in the whole
    # trend is obvious ("where is THIS analysis on the graph?").
    highlighted = False
    if highlight_date is not None:
        hits = [
            (x, y) for x, y, p in zip(xs, ys, numeric, strict=True) if p.taken_on == highlight_date
        ]
        if hits:
            hx, hy = hits[-1]
            ax.axvline(hx, color=_BRAND_DARK, linestyle=":", linewidth=1.2, alpha=0.6, zorder=1)
            ax.scatter(
                [hx],
                [hy],
                marker="o",
                s=260,
                facecolors="none",
                edgecolors=_BRAND_DARK,
                linewidths=2.2,
                zorder=6,
            )
            ax.annotate(
                locale.CHART_THIS_REPORT,
                (hx, hy),
                textcoords="offset points",
                xytext=(0, -14),
                ha="center",
                va="top",
                fontsize=8,
                fontweight="bold",
                color=_BRAND_DARK,
            )
            highlighted = True

    if numeric[-1].unit:
        ax.set_ylabel(numeric[-1].unit)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    # Tick ONLY on the dates we actually measured — otherwise matplotlib auto-fills "nice" dates
    # (e.g. a 2022 tick between a 2021 and a 2023 sample) and reads as phantom measurements — but
    # thin them so time-clustered samples don't overlap into an unreadable smear.
    ax.set_xticks(_readable_ticks(xs))
    ax.margins(x=0.08)  # a little breathing room so the first/last marker isn't clipped
    fig.autofmt_xdate()

    handles: list[object] = []
    if lo is not None or hi is not None:
        handles.append(
            Patch(facecolor=_GREEN_ZONE, edgecolor=_BOUND, label=locale.CHART_LEGEND_RANGE)
        )
    if in_x:
        handles.append(
            Line2D(
                [],
                [],
                marker="o",
                linestyle="none",
                markerfacecolor=_OK,
                markeredgecolor="white",
                label=locale.CHART_LEGEND_OK,
            )
        )
    if out_x:
        handles.append(
            Line2D(
                [],
                [],
                marker="X",
                linestyle="none",
                markerfacecolor=_OUT,
                markeredgecolor="white",
                label=locale.CHART_LEGEND_OUT,
            )
        )
    if highlighted:
        handles.append(
            Line2D(
                [],
                [],
                marker="o",
                linestyle="none",
                markerfacecolor="none",
                markeredgecolor=_BRAND_DARK,
                label=locale.CHART_LEGEND_THIS,
            )
        )
    if handles:
        ax.legend(handles=handles, fontsize=8, loc="best", framealpha=0.9)


# --- One PDF with every chart + a short description (on-demand export) ------------

_A4 = (8.27, 11.69)  # inches, portrait
_BRAND = "#16a34a"  # the same green as the in-range chart marker
_BRAND_DARK = "#14532d"
_INK = "#0f172a"  # near-black text
_MUTED = "#64748b"  # secondary text
_PANEL = "#f1f5f9"  # light card background

# matplotlib's PDF font (DejaVu Sans) has no emoji glyphs — they render as tofu boxes. Strip emoji /
# symbol / arrow / variation-selector chars from PDF text, keeping normal Cyrillic + punctuation
# (em dash —, ellipsis …, middle dot ·) which DejaVu does have.
_PDF_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff"  # emoji
    "☀-➿"  # miscellaneous symbols + dingbats
    "←-⇿"  # arrows
    "⬀-⯿"  # misc symbols & arrows
    "️⃣]"  # emoji variation selector + combining keycap
)


def _pdf_text(text: str) -> str:
    return re.sub(r"  +", " ", _PDF_EMOJI_RE.sub("", text)).strip()


def _wrap(text: str, width: int = 80) -> str:
    """Hard-wrap each paragraph to a character width that fits the description card. matplotlib's
    own ``wrap=True`` wraps to the FIGURE edge (past the card), so a long note ran off the page —
    we wrap to the card instead, preserving blank lines between paragraphs."""
    lines = [textwrap.fill(p, width=width) if p.strip() else "" for p in text.split("\n")]
    return "\n".join(lines)


def _clip(text: str, limit: int = 42) -> str:
    """Keep a header title on one line in the band."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _load_avatar() -> Any:
    """The Дбайло avatar (the README icon), bundled as package data, as an image array. None if it
    can't be read, so the PDF still renders without it."""
    try:
        from importlib.resources import files

        with files("dbaylo").joinpath("assets/dbaylo-avatar.png").open("rb") as fh:
            return plt.imread(io.BytesIO(fh.read()), format="png")
    except (FileNotFoundError, ModuleNotFoundError, OSError, ValueError):
        return None


_AVATAR: Any = _load_avatar()


def _place_avatar(fig: Figure, rect: tuple[float, float, float, float]) -> None:
    """Draw the circular avatar inside ``rect`` (figure coords), masked to a clean circle."""
    if _AVATAR is None:
        return
    ax = fig.add_axes(rect)
    ax.imshow(_AVATAR)
    ax.set_aspect("equal")
    ax.axis("off")
    circle = Circle((0.5, 0.5), 0.5, transform=ax.transAxes)
    ax.images[0].set_clip_path(circle)


@dataclass(frozen=True)
class PdfChart:
    """One page of the PDF export: an analyte's series, its title/panel, and a short description.
    ``highlight_date`` rings the measurement of the report the PDF was built from (same 'цей аналіз'
    marker as the per-chart PNGs), so the report you exported from is visible on every page."""

    title: str
    subtitle: str  # the panel / clinical group this marker belongs to
    points: list[LabPoint]
    caption: str
    highlight_date: date | None = None


@dataclass(frozen=True)
class PdfCover:
    """The PDF's title page content. Built by the caller so the rendering stays presentation-only:
    a heading, the report it is built from, a one-line numeric summary, the per-category breakdown
    of the charted indicators, and any explanatory notes (qualitative / single-measurement / total
    counts) that make the 'why only N of M indicators' honest at a glance."""

    heading: str
    report_line: str
    summary_line: str = ""
    category_rows: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PdfQualTrend:
    """One qualitative indicator on the text-timeline section: it has no numeric chart but a dated
    sequence of text results ('не виявлені' → 'виявлено'). Rendered as text, never a chart."""

    title: str
    subtitle: str  # the clinical group / specimen
    rows: tuple[tuple[str, str, bool], ...]  # (date, text, flagged) — chronological
    note: str = ""
    changed: bool = False


def _cover(pdf: PdfPages, cover: PdfCover) -> None:
    fig = plt.figure(figsize=_A4)
    try:
        fig.patches.append(
            Rectangle((0, 0.82), 1, 0.18, transform=fig.transFigure, color=_BRAND, zorder=0)
        )
        fig.patches.append(
            Rectangle((0, 0), 1, 0.05, transform=fig.transFigure, color=_BRAND, zorder=0)
        )
        _place_avatar(fig, (0.5 - 0.16, 0.60, 0.32, 0.20))
        fig.text(
            0.5, 0.52, _pdf_text(cover.heading), ha="center", fontsize=26, color=_INK, weight="bold"
        )
        if cover.report_line:
            fig.text(
                0.5, 0.475, _pdf_text(cover.report_line), ha="center", fontsize=13, color=_MUTED
            )
        y = 0.41
        if cover.summary_line:
            fig.text(
                0.5,
                y,
                _pdf_text(cover.summary_line),
                ha="center",
                fontsize=13,
                color=_BRAND_DARK,
                weight="bold",
            )
            y -= 0.045
        for row in cover.category_rows:
            fig.text(0.5, y, _pdf_text(row), ha="center", fontsize=12, color=_INK)
            y -= 0.030
        if cover.notes:
            y -= 0.018
        for note in cover.notes:
            wrapped = _wrap(_pdf_text(note), 78)
            fig.text(0.5, y, wrapped, ha="center", va="top", fontsize=10.5, color=_MUTED)
            y -= 0.026 * (wrapped.count("\n") + 1) + 0.012
        fig.text(
            0.5,
            0.085,
            _wrap(_pdf_text(locale.DISCLAIMER), 95),
            ha="center",
            fontsize=9,
            color=_MUTED,
        )
        pdf.savefig(fig)
    finally:
        plt.close(fig)


def _chart_page(pdf: PdfPages, chart: PdfChart, *, page_no: int, total: int) -> None:
    numeric = sorted((p for p in chart.points if p.value is not None), key=lambda p: p.taken_on)
    fig = plt.figure(figsize=_A4)
    try:
        # Header band with the marker name + its panel.
        fig.patches.append(
            Rectangle((0, 0.90), 1, 0.10, transform=fig.transFigure, color=_BRAND, zorder=0)
        )
        fig.text(
            0.08, 0.945, _clip(_pdf_text(chart.title)), fontsize=18, color="white", weight="bold"
        )
        if chart.subtitle:
            fig.text(0.08, 0.915, _pdf_text(chart.subtitle), fontsize=11, color="#dcfce7")

        ax = fig.add_axes((0.10, 0.42, 0.82, 0.42))
        if numeric:
            _draw(ax, fig, numeric, highlight_date=chart.highlight_date)
        ax.grid(True, alpha=0.3, zorder=0)

        # Description in a soft card (text wrapped to the card, not the page edge).
        fig.patches.append(
            Rectangle((0.07, 0.10), 0.86, 0.24, transform=fig.transFigure, color=_PANEL, zorder=0)
        )
        fig.text(
            0.10,
            0.315,
            _wrap(_pdf_text(chart.caption)),
            ha="left",
            va="top",
            fontsize=11,
            color=_INK,
        )
        fig.text(0.93, 0.045, f"{page_no}/{total}", ha="right", fontsize=9, color=_MUTED)
        fig.text(0.07, 0.045, "Дбайло", ha="left", fontsize=9, color=_MUTED)
        pdf.savefig(fig)
    finally:
        plt.close(fig)


_QUAL_TOP = 0.85
_QUAL_BOTTOM = 0.09
_QUAL_LH = 0.026  # one text line's height in figure coords


def _qual_pages(pdf: PdfPages, items: list[PdfQualTrend], *, heading: str) -> None:
    """Text-timeline pages for qualitative indicators (no numeric chart). Several indicators are
    packed per page; a block is kept whole — when it won't fit, a new page is started. So a urine
    'не виявлені' that becomes 'виявлено' is still visible in dynamics, as honest text."""
    if not items:
        return
    state: dict[str, Any] = {"fig": None, "y": 0.0}

    def _open() -> None:
        fig = plt.figure(figsize=_A4)
        fig.patches.append(
            Rectangle((0, 0.92), 1, 0.08, transform=fig.transFigure, color=_BRAND, zorder=0)
        )
        fig.text(0.08, 0.945, _pdf_text(heading), fontsize=15, color="white", weight="bold")
        fig.patches.append(
            Rectangle((0, 0), 1, 0.04, transform=fig.transFigure, color=_BRAND, zorder=0)
        )
        fig.text(0.07, 0.05, "Дбайло", ha="left", fontsize=9, color=_MUTED)
        state["fig"], state["y"] = fig, _QUAL_TOP

    def _close() -> None:
        if state["fig"] is not None:
            pdf.savefig(state["fig"])
            plt.close(state["fig"])
            state["fig"] = None

    for it in items:
        rows = it.rows[-6:]  # keep the page readable; the recent history is what matters
        note_wrapped = _wrap(_pdf_text(it.note), 90) if it.note else ""
        block_h = (
            _QUAL_LH * (2 + len(rows))
            + (_QUAL_LH * (note_wrapped.count("\n") + 1) if note_wrapped else 0)
            + 0.018
        )
        if state["fig"] is None or state["y"] - block_h < _QUAL_BOTTOM:
            _close()
            _open()
        fig, y = state["fig"], state["y"]
        fig.text(0.08, y, _clip(_pdf_text(it.title), 56), fontsize=13, color=_INK, weight="bold")
        y -= _QUAL_LH
        sub = it.subtitle
        if it.changed:
            changed = locale.CHART_PDF_QUAL_CHANGED
            sub = f"{sub}  ·  {changed}" if sub else changed
        fig.text(0.08, y, _pdf_text(sub), fontsize=9.5, color=_MUTED)
        y -= _QUAL_LH
        for d, text, flagged in rows:
            line = locale.CHART_PDF_QUAL_ROW.format(date=d, text=text)
            fig.text(0.10, y, _pdf_text(f"• {line}"), fontsize=11, color=_OUT if flagged else _INK)
            y -= _QUAL_LH
        if note_wrapped:
            fig.text(0.10, y, note_wrapped, ha="left", va="top", fontsize=9.5, color=_MUTED)
            y -= _QUAL_LH * (note_wrapped.count("\n") + 1)
        state["y"] = y - 0.018
    _close()


def render_trends_pdf(
    charts: list[PdfChart], *, cover: PdfCover, qual_trends: tuple[PdfQualTrend, ...] = ()
) -> bytes:
    """A single, premium PDF: a branded cover (avatar + report context + honest breakdown), then ONE
    trend chart per page with a description card, then a text-timeline section for the qualitative
    indicators that have no numeric chart. The 'save everything' counterpart to the per-chart
    picker; same chart language as the PNGs."""
    buffer = io.BytesIO()
    with PdfPages(buffer) as pdf:
        _cover(pdf, cover)
        for i, chart in enumerate(charts, 1):
            _chart_page(pdf, chart, page_no=i, total=len(charts))
        _qual_pages(pdf, list(qual_trends), heading=locale.CHART_PDF_QUAL_HEADING)
    return buffer.getvalue()
