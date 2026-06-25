"""SQLAlchemy 2.0 models — one mapped class per discovery "Data model" entity.

Typed with ``Mapped[...]`` / ``mapped_column``. Single-user to start, but every
record is already user-scoped so multi-user is a later additive change.

Note (safety rail #1): ``Medication.dose`` / ``Medication.schedule`` store what a
doctor prescribed — record-keeping, not prescribing. The "no dose directive"
rail constrains what Дбайло *says* (see ``triage.safety``), not what is stored.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, Text, false, func, true
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dbaylo.db.base import Base


class GoalStatus(StrEnum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    PAUSED = "paused"
    ABANDONED = "abandoned"


class ConditionStatus(StrEnum):
    """Whether a health concern is currently active.

    The daily check-in is scheduled iff at least one ACTIVE condition exists
    (Tier 1.1 — conditional, never an unconditional daily ping).
    """

    ACTIVE = "active"
    RESOLVED = "resolved"


class ResultFlag(StrEnum):
    """Where a lab value sits relative to its reference range."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    UNKNOWN = "unknown"


class ReportStatus(StrEnum):
    """Lifecycle of a lab report through the OCR-confirmation loop (rail #2).

    A report is PENDING from intake until the user confirms the extracted values;
    only then are LabResult rows written and the report marked CONFIRMED.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class ReportKind(StrEnum):
    """Whether a report is an analyte TABLE or a NARRATIVE document (Stage 6).

    TABULAR reports have LabResult rows and feed the trend engine. NARRATIVE reports
    (МРТ / УЗД / КТ / висновок / виписка) have no analytes — only a findings text and a
    conclusion — and are never fed to trends.
    """

    TABULAR = "tabular"
    NARRATIVE = "narrative"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(unique=True, index=True, default=None)
    name: Mapped[str | None] = mapped_column(default=None)

    lab_reports: Mapped[list[LabReport]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    medications: Mapped[list[Medication]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    conditions: Mapped[list[Condition]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    reminders: Mapped[list[Reminder]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    check_ins: Mapped[list[CheckIn]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    goals: Mapped[list[Goal]] = relationship(back_populates="user", cascade="all, delete-orphan")
    consult_memories: Mapped[list[ConsultMemory]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class LabReport(TimestampMixin, Base):
    __tablename__ = "lab_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    report_date: Mapped[date | None] = mapped_column(Date, default=None)
    lab: Mapped[str | None] = mapped_column(default=None)
    # The patient's date of birth, when the report header carries it. Used to resolve AGE-STRATIFIED
    # references (e.g. ПСА: <40 -> <1.4) deterministically — the lab's own age table, picked by age.
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    # The patient's sex ("m"/"f"), when the header carries it. Used to pick the right band from a
    # SEX-split reference (e.g. RBC/HGB "Дорослі: Чоловіки …; Жінки …"). Never shown to the user.
    sex: Mapped[str | None] = mapped_column(String(1), default=None)
    # Original file is always kept and linked (safety rail #2: OCR never trusted silently).
    source_file: Mapped[str | None] = mapped_column(default=None)
    # SHA-256 of the uploaded bytes, so re-uploading the exact same file is detected and not
    # re-extracted (no duplicate report / degenerate same-day "trend"). Nullable for old rows.
    content_hash: Mapped[str | None] = mapped_column(default=None, index=True)
    raw_ocr: Mapped[str | None] = mapped_column(Text, default=None)
    # Stage 5: the lab's own overall conclusion (e.g. "Нормозооспермія") and Дбайло's
    # generated expert summary, persisted so /history can show them without re-calling the LLM.
    conclusion: Mapped[str | None] = mapped_column(Text, default=None)
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    # Stage 6: TABULAR (analyte table) vs NARRATIVE (МРТ/УЗД/висновок — findings text, no
    # analytes). report_type is the human label ("МРТ головного мозку"); narrative is the
    # extracted findings body.
    kind: Mapped[ReportKind] = mapped_column(
        SAEnum(ReportKind), default=ReportKind.TABULAR, server_default=ReportKind.TABULAR.name
    )
    report_type: Mapped[str | None] = mapped_column(default=None)
    narrative: Mapped[str | None] = mapped_column(Text, default=None)
    # PENDING until the user confirms the extracted values; CONFIRMED writes results.
    status: Mapped[ReportStatus] = mapped_column(SAEnum(ReportStatus), default=ReportStatus.PENDING)

    user: Mapped[User] = relationship(back_populates="lab_reports")
    results: Mapped[list[LabResult]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class LabResult(Base):
    __tablename__ = "lab_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("lab_reports.id", ondelete="CASCADE"))
    analyte: Mapped[str] = mapped_column()
    value: Mapped[float | None] = mapped_column(Float, default=None)
    # Qualitative result as printed ("негативно", "не виявлено", "++") when there is no number.
    value_text: Mapped[str | None] = mapped_column(default=None)
    unit: Mapped[str | None] = mapped_column(default=None)
    ref_low: Mapped[float | None] = mapped_column(Float, default=None)
    ref_high: Mapped[float | None] = mapped_column(Float, default=None)
    # The reference VERBATIM as printed (kept even when ref_low/ref_high are derived from it).
    ref_text: Mapped[str | None] = mapped_column(default=None)
    flag: Mapped[ResultFlag] = mapped_column(SAEnum(ResultFlag), default=ResultFlag.UNKNOWN)
    # Stage 5: the lab's own out-of-range indicator (or a numeric value outside its
    # reference). Drives the ⚠️/✅ marker (a flag-free row is shown as ✅, "ok").
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The panel this row belongs to (e.g. "Загальний аналіз крові" / "Загальний аналіз сечі"),
    # so a combined report renders its groups apart and a name in two panels is never confused.
    section: Mapped[str | None] = mapped_column(default=None)

    report: Mapped[LabReport] = relationship(back_populates="results")


class Medication(TimestampMixin, Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column()
    # Record-keeping of a clinician's prescription — see module note / rail #1.
    dose: Mapped[str | None] = mapped_column(default=None)
    schedule: Mapped[str | None] = mapped_column(default=None)
    prescribed_by: Mapped[str | None] = mapped_column(default=None)

    user: Mapped[User] = relationship(back_populates="medications")


class Condition(TimestampMixin, Base):
    __tablename__ = "conditions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[ConditionStatus] = mapped_column(
        SAEnum(ConditionStatus), default=ConditionStatus.ACTIVE
    )
    # When the check-in last asked "still relevant?" — so an active concern is
    # periodically offered for closure instead of pinging forever (Tier 1.1 §B).
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Set when a concern was proposed from an out-of-range lab flag, so deleting that
    # report can clean up / surface the coupling instead of leaving it pinging (Tier 1.2).
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("lab_reports.id", ondelete="SET NULL"), default=None
    )

    user: Mapped[User] = relationship(back_populates="conditions")


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column()
    schedule: Mapped[str] = mapped_column()
    payload: Mapped[str | None] = mapped_column(Text, default=None)
    # Set for medication reminders: one Medication maps to one reminder per dose
    # time, so turning a medication off deactivates every reminder that links here.
    medication_id: Mapped[int | None] = mapped_column(
        ForeignKey("medications.id", ondelete="CASCADE"), default=None
    )
    # Set for a repeat-lab reminder created from a report, so deleting that report can
    # retire the reminder too (Tier 1.2 — no orphaned "repeat this lab" pings).
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("lab_reports.id", ondelete="SET NULL"), default=None
    )
    # Reminder rows are the scheduler's source of truth (rebuilt on startup);
    # a soft-delete flag lets a fired one-off be retired without losing the record.
    # next_run is intentionally NOT stored — APScheduler computes it (a DB copy
    # would go stale); read it from the built scheduler when displaying.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # When this reminder last fired (Stage 6 durability). On startup the scheduler
    # delivers any occurrence that came due since this anchor while the process was
    # down — so reminders are not lost across a restart. Updated on every fire.
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    user: Mapped[User] = relationship(back_populates="reminders")


class CheckIn(TimestampMixin, Base):
    __tablename__ = "check_ins"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    check_date: Mapped[date | None] = mapped_column(Date, default=None)
    sleep_hours: Mapped[float | None] = mapped_column(Float, default=None)
    water_ml: Mapped[int | None] = mapped_column(default=None)
    mood: Mapped[int | None] = mapped_column(default=None)
    symptoms: Mapped[str | None] = mapped_column(Text, default=None)
    training: Mapped[str | None] = mapped_column(Text, default=None)

    user: Mapped[User] = relationship(back_populates="check_ins")


class Goal(TimestampMixin, Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column()
    target: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[GoalStatus] = mapped_column(SAEnum(GoalStatus), default=GoalStatus.ACTIVE)

    user: Mapped[User] = relationship(back_populates="goals")


class IndicatorNote(TimestampMixin, Base):
    """A persisted educational note about an indicator ("що це / до чого призводить відхилення").

    The note is a pure function of (persona version, specimen, normalized analyte name) — it does
    NOT depend on any measured values — so it is GLOBAL (not per-user) and never goes stale by new
    data. It is generated once by claude and reused forever (across restarts, reports, the PDF), so
    charts/tables can carry a description without the user ever waiting twice. Bumping the persona
    version changes the key, so old notes are simply ignored (and re-generated)."""

    __tablename__ = "indicator_notes"

    cache_key: Mapped[str] = mapped_column(primary_key=True)  # version \x1f specimen \x1f analyte
    text: Mapped[str] = mapped_column(Text)


class ConsultMemory(TimestampMixin, Base):
    """A durable, cross-session memory of a contextual consultation ("Запитати Дбайло").

    Each row is ONE turn — the user's question or Дбайло's answer — from a grounded consultation.
    Unlike the in-flight FSM transcript (which is cleared the moment a consultation ends), these
    persist, so a LATER consultation can recall what was discussed before: real continuity, not a
    cold start. Deterministic record-keeping — plain text, written and read by ``consult_memory``,
    never fed to an escalation engine. ``report_id`` ties a memory to the report it was about
    (``SET NULL`` on delete, like Condition/Reminder), so deleting a report DECOUPLES — but does not
    silently erase — the conversation that was had about it."""

    __tablename__ = "consult_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("lab_reports.id", ondelete="SET NULL"), default=None
    )
    role: Mapped[str] = mapped_column()  # "user" | "assistant"
    text: Mapped[str] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="consult_memories")
