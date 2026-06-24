"""Grounded context builder (``companion.consult_context``) — deterministic, DB-only, no LLM.

The consult answers from real data, so the context must faithfully carry the subject's measurements
(indicator) or the panel table + saved reading (report), and resolve to ``None`` when the subject is
gone. These are async DB tests (the in-memory session fixture)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.companion.consult_context import (
    KIND_INDICATOR,
    KIND_REPORT,
    KIND_SECTION,
    Subject,
    build_context,
    section_label,
)
from dbaylo.labs.intake import create_pending_report, ensure_user, persist_confirmed
from dbaylo.labs.schema import ExtractedAnalyte
from dbaylo.labs.trends import series_key

_TODAY = date(2026, 6, 24)


def _analyte(name, value, low=None, high=None, unit="ммоль/л", section=None):
    return ExtractedAnalyte(
        analyte=name, value=value, unit=unit, ref_low=low, ref_high=high, section=section
    )


async def _confirm(session: AsyncSession, user, day: int, value: float):
    report = await create_pending_report(session, user=user, file_path=Path(f"/tmp/{day}.png"))
    await persist_confirmed(
        session,
        report=report,
        analytes=[_analyte("Холестерин", value, None, 5.2)],
        report_date=date(2023, 1, day),
        lab="Synevo",
    )
    return report


def test_subject_roundtrips_through_a_dict() -> None:
    s = Subject(kind=KIND_INDICATOR, report_id=7, analyte_key="k", analyte_name="Глюкоза")
    assert Subject.from_dict(s.to_dict()) == s
    sec = Subject(kind=KIND_SECTION, report_id=3, section_idx=2)
    assert Subject.from_dict(sec.to_dict()) == sec  # section index survives the round-trip
    # A malformed dict degrades safely (no crash) rather than raising.
    assert Subject.from_dict({}).kind == "" and Subject.from_dict({}).section_idx == -1


def test_section_label_maps_index_to_name() -> None:
    assert section_label(0) == locale.INTERPRET_SECTION_OVERALL
    assert section_label(2) == locale.INTERPRET_SECTION_HELP
    assert section_label(99) == "" and section_label(-1) == ""


async def test_indicator_context_carries_values_dates_status_and_trend(
    async_session: AsyncSession,
) -> None:
    user = await ensure_user(async_session, 1)
    await _confirm(async_session, user, 1, 6.2)  # above ≤5.2 -> out of range
    await _confirm(async_session, user, 10, 5.0)  # back in range
    key = series_key(None, "Холестерин")
    subject = Subject(kind=KIND_INDICATOR, report_id=0, analyte_key=key, analyte_name="Холестерин")
    built = await build_context(async_session, user.id, subject, today=_TODAY)
    assert built is not None
    context, label = built
    assert label == "Холестерин"
    assert "Холестерин" in context
    assert "2023-01-01" in context and "2023-01-10" in context  # both measurements
    assert "OUT OF RANGE" in context and "in range" in context  # per-point status
    assert "trend" in context.lower()  # the range-relative direction


async def test_report_context_carries_the_panel_table(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/r.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)],
        report_date=date(2023, 5, 2),
        lab="Synevo",
    )
    subject = Subject(kind=KIND_REPORT, report_id=report.id)
    built = await build_context(async_session, user.id, subject, today=_TODAY)
    assert built is not None
    context, label = built
    assert "2023-05-02" in label and "Сінево" in label  # canonicalized lab in the label
    assert "Глюкоза" in context and "7" in context  # the value is grounded
    assert "ATTENTION" in context  # 7.0 > 6.1 -> the lab table marks it


async def test_section_context_focuses_on_the_section_and_labels_it(
    async_session: AsyncSession,
) -> None:
    user = await ensure_user(async_session, 1)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/s.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)],
        report_date=date(2023, 5, 2),
        lab="Synevo",
    )
    # Section index 2 == "Що допоможе": context keeps the full report data, plus a focus line.
    subject = Subject(kind=KIND_SECTION, report_id=report.id, section_idx=2)
    built = await build_context(async_session, user.id, subject, today=_TODAY)
    assert built is not None
    context, label = built
    assert label == locale.INTERPRET_SECTION_HELP  # the subject label is the section's name
    assert "Глюкоза" in context  # the full grounded report data is still there
    assert locale.INTERPRET_SECTION_HELP in context  # the focus names the section


async def test_context_prepends_a_state_aware_patient_profile(
    async_session: AsyncSession,
) -> None:
    # The consult acts like an assistant who knows the patient: every context carries a profile —
    # age/sex (from the report), today's date, tracked concerns, and recent reports WITH dates.
    from dbaylo.companion import concerns

    user = await ensure_user(async_session, 1)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/p.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)],
        report_date=date(2023, 5, 2),
        lab="Synevo",
        birth_date=date(1993, 3, 24),
        sex="m",
    )
    await concerns.add_active(async_session, user=user, name="Камені в нирках")

    subject = Subject(kind=KIND_REPORT, report_id=report.id)
    context, _label = await build_context(async_session, user.id, subject, today=_TODAY)  # type: ignore[misc]
    assert "PATIENT PROFILE" in context
    assert _TODAY.isoformat() in context  # today's date, so the model can judge recency
    assert "~33 years old" in context and "male" in context  # 1993-03-24 -> 33 on 2026-06-24
    assert "Камені в нирках" in context  # the tracked concern
    assert "2023-05-02" in context  # the recent report's date


async def test_missing_subject_resolves_to_none(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    # A report id that does not exist, and an unknown analyte key -> None, never a crash.
    assert (
        await build_context(async_session, user.id, Subject(KIND_REPORT, 999_999), today=_TODAY)
        is None
    )
    gone = Subject(kind=KIND_INDICATOR, report_id=0, analyte_key="nope", analyte_name="Невідомо")
    assert await build_context(async_session, user.id, gone, today=_TODAY) is None
