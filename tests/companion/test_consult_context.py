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
    KIND_GENERAL,
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


async def test_patient_profile_addresses_the_user_by_name(async_session: AsyncSession) -> None:
    # The name is fed to EVERY voice via the profile, so Дбайло addresses the user warmly (vocative,
    # varied forms) — even for a brand-new user with no data yet (name alone yields a profile).
    from dbaylo.companion.consult_context import patient_profile

    user = await ensure_user(async_session, 1, "Володимир")
    profile = await patient_profile(async_session, user.id, _TODAY)
    assert "Володимир" in profile
    assert "VOCATIVE" in profile  # the how-to-address instruction rides the profile


async def test_persist_confirmed_adopts_the_patient_name(async_session: AsyncSession) -> None:
    # A confirmed report's given name is adopted onto User.name (the medical doc is authoritative),
    # replacing the Telegram handle so later chat uses the real name.
    user = await ensure_user(async_session, 1, "tg_handle")
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/n.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[_analyte("Холестерин", 5.0, None, 5.2)],
        report_date=date(2026, 1, 1),
        lab="Synevo",
        patient_name="Володимир",
    )
    assert user.name == "Володимир"


async def test_patient_profile_includes_active_goals(async_session: AsyncSession) -> None:
    # Goals are now FUNCTIONAL: an active goal is grounded into the profile, so Дбайло references &
    # supports it everywhere (chat / consult / check-in), not just stores a dead row.
    from dbaylo.companion import goals
    from dbaylo.companion.consult_context import patient_profile

    user = await ensure_user(async_session, 1)
    result = await goals.set_goal(async_session, user=user, text="Налагодити режим сну")
    assert result.saved  # a benign wellness goal passes the guardrail
    profile = await patient_profile(async_session, user.id, _TODAY)
    assert "Goals the user is actively working toward" in profile
    assert "Налагодити режим сну" in profile


async def test_general_context_grounds_a_whole_picture_consult(async_session: AsyncSession) -> None:
    # #6: a general consultation (entered from chat's affordances) grounds in the indicator picture.
    user = await ensure_user(async_session, 1)
    await _confirm(async_session, user, 1, 6.2)  # Холестерин above ≤5.2 -> currently out of range
    built = await build_context(
        async_session, user.id, Subject(kind=KIND_GENERAL, report_id=0), today=_TODAY
    )
    assert built is not None
    context, label = built
    assert label == locale.CONSULT_GENERAL_LABEL
    assert "OVERALL health picture" in context  # the whole-picture framing
    assert "Холестерин" in context  # the real out-of-range indicator is carried in


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


async def test_patient_profile_is_empty_without_any_data(async_session: AsyncSession) -> None:
    # Grounding fallback: a user with no age/sex, no concerns and no reports yields "" so the
    # general companion / intake answers without a (non-existent) profile.
    from dbaylo.companion.consult_context import patient_profile

    user = await ensure_user(async_session, 1)
    assert await patient_profile(async_session, user.id, _TODAY) == ""


async def test_patient_profile_carries_concerns_and_reports(async_session: AsyncSession) -> None:
    from dbaylo.companion import concerns
    from dbaylo.companion.consult_context import patient_profile

    user = await ensure_user(async_session, 1)
    await _confirm(async_session, user, 5, 6.2)
    await concerns.add_active(async_session, user=user, name="Камені в нирках")
    profile = await patient_profile(async_session, user.id, _TODAY)
    assert "Камені в нирках" in profile  # tracked concern is grounded
    assert "2023-01-05" in profile  # the recent report's date


async def test_missing_subject_resolves_to_none(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    # A report id that does not exist, and an unknown analyte key -> None, never a crash.
    assert (
        await build_context(async_session, user.id, Subject(KIND_REPORT, 999_999), today=_TODAY)
        is None
    )
    gone = Subject(kind=KIND_INDICATOR, report_id=0, analyte_key="nope", analyte_name="Невідомо")
    assert await build_context(async_session, user.id, gone, today=_TODAY) is None


async def _narrative(session, user, *, day: date, report_type: str, findings: str, verdict: str):
    report = await create_pending_report(session, user=user, file_path=Path(f"/tmp/n{day}.png"))
    await persist_confirmed(
        session,
        report=report,
        analytes=[],
        report_date=day,
        lab="Urosvit",
        report_type=report_type,
        narrative=findings,
        conclusion=verdict,
    )
    return report


def test_clip_body_collapses_wraps_and_cuts_on_a_word_boundary() -> None:
    from dbaylo.companion.consult_context import _clip_body

    # OCR hard-wraps are collapsed so the findings read as flowing text.
    assert _clip_body("Права\n  нирка:\tконкремент 7мм") == "Права нирка: конкремент 7мм"
    # A long body is cut on a word boundary (never mid-number, which would fake a measurement).
    clipped = _clip_body("слово " * 400, limit=50)
    assert len(clipped) <= 51 and clipped.endswith("…") and not clipped.endswith("сло…")


async def test_documents_block_carries_the_findings_text(async_session: AsyncSession) -> None:
    # The reported gap: a УЗД/КТ has NO analyte rows, so without this block the model knows only
    # that the exam exists — it cannot say how many stones it described.
    from dbaylo.companion.consult_context import _documents_block

    user = await ensure_user(async_session, 1)
    assert await _documents_block(async_session, user.id, _TODAY) == ""  # none yet
    await _narrative(
        async_session,
        user,
        day=date(2026, 1, 19),
        report_type="УЗД нирок",
        findings="Права нирка: конкременти 7мм та 5мм. Ліва нирка: конкременти 8мм, 6,5мм, 6,7мм.",
        verdict="УЗ-ознаки двобічного нефролітіазу.",
    )
    block = await _documents_block(async_session, user.id, _TODAY)
    assert "УЗД нирок" in block and "2026-01-19" in block
    assert "конкременти 7мм та 5мм" in block  # the actual FINDINGS, not just the date
    assert "нефролітіазу" in block  # the doctor's own conclusion
    assert "COMPARING" in block  # the instruction that unlocks a cross-exam answer


async def test_profile_lets_two_exams_be_compared(async_session: AsyncSession) -> None:
    # The exact failing scenario: "порівняй з кількістю які були у січні" — BOTH exams' texts must
    # be in the grounded profile, so the answer is a comparison, not "I don't have that report".
    from dbaylo.companion.consult_context import patient_profile

    user = await ensure_user(async_session, 1)
    await _narrative(
        async_session,
        user,
        day=date(2026, 1, 19),
        report_type="УЗД нирок",
        findings="Ліва нирка: конкременти 8мм, 6,5мм, 6,7мм.",
        verdict="Нефролітіаз.",
    )
    await _narrative(
        async_session,
        user,
        day=date(2026, 7, 14),
        report_type="УЗД нирок повторне",
        findings="Ліва нирка: конкременти 10мм, 3,7мм, 7,9мм, 6,7мм, 4,2мм.",
        verdict="Нефролітіаз.",
    )
    profile = await patient_profile(async_session, user.id, _TODAY)
    assert "8мм, 6,5мм, 6,7мм" in profile  # January's stones
    assert "10мм, 3,7мм" in profile  # July's stones — both readable in ONE context
    assert profile.index("2026-07-14") < profile.index("2026-01-19")  # most recent first


async def test_documents_block_caps_a_long_imaging_history(async_session: AsyncSession) -> None:
    # Bounded so many exams cannot crowd out the rest of the grounding — and the truncation is
    # STATED, never silent (a silent cut would read as "that is everything").
    from dbaylo.companion.consult_context import _DOCS_MAX, _documents_block

    user = await ensure_user(async_session, 1)
    for i in range(_DOCS_MAX + 2):
        await _narrative(
            async_session,
            user,
            day=date(2026, 1, i + 1),
            report_type=f"УЗД {i}",
            findings=f"Знахідка {i}.",
            verdict="",
        )
    block = await _documents_block(async_session, user.id, _TODAY)
    assert block.count("FINDINGS:") == _DOCS_MAX
    assert "older document(s) exist" in block
