"""Per-analyte trend charts, rendered headless to PNG bytes.

Deterministic and LLM-free: it draws exactly the numbers it is given. Uses the
matplotlib Agg backend so it works on a headless VPS with no display.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # headless: must be set before pyplot is imported.

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from dbaylo.labs.trends import LabPoint  # noqa: E402


def render_trend_chart(points: list[LabPoint], *, title: str) -> bytes:
    """Render a single analyte's series to PNG bytes (value vs date)."""
    numeric = sorted((p for p in points if p.value is not None), key=lambda p: p.taken_on)
    # Plot in float coordinates (matplotlib date numbers) for clean typing.
    xs = [mdates.date2num(p.taken_on) for p in numeric]
    ys = [p.value for p in numeric if p.value is not None]

    fig, ax = plt.subplots(figsize=(7, 4), dpi=120)
    try:
        ax.plot(xs, ys, "-", color="#334155", zorder=1)
        ax.scatter(xs, ys, color="#334155", zorder=2)

        # Shade the reference band using the latest point's range, when available.
        latest = numeric[-1] if numeric else None
        if latest is not None:
            if latest.ref_low is not None and latest.ref_high is not None:
                ax.axhspan(latest.ref_low, latest.ref_high, color="#dcfce7", zorder=0)
            elif latest.ref_high is not None:
                ax.axhline(latest.ref_high, color="#16a34a", linestyle="--", linewidth=1)
            elif latest.ref_low is not None:
                ax.axhline(latest.ref_low, color="#16a34a", linestyle="--", linewidth=1)

        ax.set_title(title)
        if latest is not None and latest.unit:
            ax.set_ylabel(latest.unit)
        ax.grid(True, alpha=0.3)
        if xs:
            ax.xaxis_date()
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            fig.autofmt_xdate()

        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", bbox_inches="tight")
        return buffer.getvalue()
    finally:
        plt.close(fig)
