"""Lab intake + persistence (async).

Intake stores the original file (always kept and linked — rail #2) and creates a
PENDING LabReport. LabResult rows are written only later, by ``persist_confirmed``,
once the user has confirmed the extracted values.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.config import Settings, get_settings
from dbaylo.db.models import LabReport, LabResult, ReportStatus, User
from dbaylo.labs.schema import ExtractedAnalyte
from dbaylo.labs.trends import classify, is_out_of_range

SUPPORTED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".pdf"})


def is_supported(suffix: str) -> bool:
    return suffix.lower() in SUPPORTED_SUFFIXES


async def ensure_user(session: AsyncSession, telegram_id: int, name: str | None = None) -> User:
    """Get-or-create the single user by Telegram id."""
    existing = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if existing is not None:
        return existing
    user = User(telegram_id=telegram_id, name=name)
    session.add(user)
    await session.flush()
    return user


def save_original_file(
    data: bytes, *, user_id: int, suffix: str, settings: Settings | None = None
) -> Path:
    """Write the uploaded bytes under the storage dir; return the saved path."""
    settings = settings or get_settings()
    user_dir = settings.storage_dir / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{uuid.uuid4().hex}{suffix.lower()}"
    path.write_bytes(data)
    return path


async def create_pending_report(
    session: AsyncSession, *, user: User, file_path: Path, raw_ocr: str | None = None
) -> LabReport:
    """Create a PENDING LabReport linked to the stored original file."""
    report = LabReport(
        user_id=user.id,
        source_file=str(file_path),
        raw_ocr=raw_ocr,
        status=ReportStatus.PENDING,
    )
    session.add(report)
    await session.flush()
    return report


async def persist_confirmed(
    session: AsyncSession,
    *,
    report: LabReport,
    analytes: list[ExtractedAnalyte],
    report_date: date | None,
    lab: str | None,
    conclusion: str | None = None,
) -> list[LabResult]:
    """Write confirmed LabResult rows and mark the report CONFIRMED.

    Called only after the user has confirmed the values (rail #2). The numeric flag and
    the attention ``flagged`` mark are computed deterministically here, never by the model
    (the model only reports the lab's own out-of-range indicator, which feeds ``flagged``).
    """
    report.report_date = report_date
    report.lab = lab
    report.conclusion = conclusion
    report.status = ReportStatus.CONFIRMED

    results: list[LabResult] = []
    for a in analytes:
        result = LabResult(
            report_id=report.id,
            analyte=a.analyte,
            value=a.value,
            unit=a.unit,
            ref_low=a.ref_low,
            ref_high=a.ref_high,
            flag=classify(a.value, a.value_text, a.ref_low, a.ref_high, a.ref_text),
            flagged=is_out_of_range(a.value, a.ref_low, a.ref_high, a.out_of_range),
        )
        session.add(result)
        results.append(result)
    await session.flush()
    return results
