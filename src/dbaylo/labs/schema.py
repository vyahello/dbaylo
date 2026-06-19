"""Data carriers for extracted lab data (pre-confirmation, pre-DB).

These hold what the model returned, before the user confirms it. They are plain
dataclasses — not ORM rows — precisely because extracted values must never touch
the DB until the user confirms them (safety rail #2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class ExtractedAnalyte:
    """One extracted analyte row, as read off the form (still unconfirmed)."""

    analyte: str
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = None
    # The lab's OWN out-of-range indicator (a box / highlight / asterisk / bold / colour,
    # or a value outside the printed reference), read visually by the model. ``None`` =
    # no reference to judge by. This is OCR of the lab's verdict, not our interpretation.
    out_of_range: bool | None = None

    def display_value(self) -> str:
        """Best human rendering of the value (numeric or qualitative)."""
        if self.value is not None:
            text = f"{self.value:g}"
        elif self.value_text:
            text = self.value_text
        else:
            text = "—"
        return f"{text} {self.unit}".strip() if self.unit else text

    def display_reference(self) -> str:
        """Best human rendering of the reference range."""
        if self.ref_low is not None and self.ref_high is not None:
            return f"{self.ref_low:g}–{self.ref_high:g}"
        if self.ref_high is not None:
            return f"≤ {self.ref_high:g}"
        if self.ref_low is not None:
            return f"≥ {self.ref_low:g}"
        return self.ref_text or "—"


@dataclass
class ExtractedReport:
    """A whole extracted report: metadata + the analyte rows."""

    results: list[ExtractedAnalyte] = field(default_factory=list)
    report_date: date | None = None
    lab: str | None = None
    # The report's own overall conclusion if it prints one (e.g. "Нормозооспермія").
    conclusion: str | None = None

    def flagged_results(self) -> list[ExtractedAnalyte]:
        """Rows the lab marked out of range (its own attention indicator)."""
        return [a for a in self.results if a.out_of_range]
