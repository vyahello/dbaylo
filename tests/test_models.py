"""Schema smoke tests: models map, persist, and relate as expected."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from dbaylo.db.models import (
    CheckIn,
    Goal,
    GoalStatus,
    LabReport,
    LabResult,
    Medication,
    ResultFlag,
    User,
)


def test_user_round_trip(session: Session) -> None:
    user = User(telegram_id=123, name="Test")
    session.add(user)
    session.commit()

    loaded = session.scalar(select(User).where(User.telegram_id == 123))
    assert loaded is not None
    assert loaded.name == "Test"


def test_lab_report_result_relationship(session: Session) -> None:
    user = User(telegram_id=1)
    report = LabReport(user=user, report_date=date(2026, 1, 1), lab="Synevo")
    report.results.append(
        LabResult(analyte="Creatinine", value=95.0, unit="umol/L", ref_low=64, ref_high=104)
    )
    session.add(user)
    session.commit()

    loaded = session.scalar(select(LabReport))
    assert loaded is not None
    assert loaded.lab == "Synevo"
    assert loaded.results[0].analyte == "Creatinine"
    assert loaded.results[0].flag == ResultFlag.UNKNOWN  # enum default


def test_medication_stores_prescribed_dose(session: Session) -> None:
    """Record-keeping of a prescription is allowed data (rail #1 governs output)."""
    user = User(telegram_id=2)
    user.medications.append(
        Medication(name="Tamsulosin", dose="0.4 mg", schedule="once daily", prescribed_by="Dr. X")
    )
    session.add(user)
    session.commit()

    med = session.scalar(select(Medication))
    assert med is not None
    assert med.dose == "0.4 mg"


def test_goal_and_checkin_defaults(session: Session) -> None:
    user = User(telegram_id=3)
    user.goals.append(Goal(type="sleep", target="8h"))
    user.check_ins.append(CheckIn(check_date=date(2026, 1, 2), sleep_hours=7.5, mood=4))
    session.add(user)
    session.commit()

    goal = session.scalar(select(Goal))
    assert goal is not None
    assert goal.status == GoalStatus.ACTIVE


def test_cascade_delete_removes_children(session: Session) -> None:
    user = User(telegram_id=4)
    user.medications.append(Medication(name="X"))
    session.add(user)
    session.commit()

    session.delete(user)
    session.commit()
    assert session.scalar(select(Medication)) is None
