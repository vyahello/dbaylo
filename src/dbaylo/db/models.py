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

from sqlalchemy import Date, DateTime, Float, ForeignKey, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dbaylo.db.base import Base


class GoalStatus(StrEnum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    PAUSED = "paused"
    ABANDONED = "abandoned"


class ResultFlag(StrEnum):
    """Where a lab value sits relative to its reference range."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    UNKNOWN = "unknown"


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


class LabReport(TimestampMixin, Base):
    __tablename__ = "lab_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    report_date: Mapped[date | None] = mapped_column(Date, default=None)
    lab: Mapped[str | None] = mapped_column(default=None)
    # Original file is always kept and linked (safety rail #2: OCR never trusted silently).
    source_file: Mapped[str | None] = mapped_column(default=None)
    raw_ocr: Mapped[str | None] = mapped_column(Text, default=None)

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
    unit: Mapped[str | None] = mapped_column(default=None)
    ref_low: Mapped[float | None] = mapped_column(Float, default=None)
    ref_high: Mapped[float | None] = mapped_column(Float, default=None)
    flag: Mapped[ResultFlag] = mapped_column(SAEnum(ResultFlag), default=ResultFlag.UNKNOWN)

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

    user: Mapped[User] = relationship(back_populates="conditions")


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column()
    schedule: Mapped[str] = mapped_column()
    payload: Mapped[str | None] = mapped_column(Text, default=None)

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
