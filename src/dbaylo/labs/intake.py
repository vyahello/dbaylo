"""Lab intake + persistence (async).

Intake stores the original file (always kept and linked — rail #2) and creates a
PENDING LabReport. LabResult rows are written only later, by ``persist_confirmed``,
once the user has confirmed the extracted values.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.config import Settings, get_settings
from dbaylo.db.models import LabReport, LabResult, ReportKind, ReportStatus, User
from dbaylo.labs.labnames import normalize_lab
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
    session: AsyncSession,
    *,
    user: User,
    file_path: Path,
    raw_ocr: str | None = None,
    content_hash: str | None = None,
) -> LabReport:
    """Create a PENDING LabReport linked to the stored original file."""
    report = LabReport(
        user_id=user.id,
        source_file=str(file_path),
        content_hash=content_hash,
        raw_ocr=raw_ocr,
        status=ReportStatus.PENDING,
    )
    session.add(report)
    await session.flush()
    return report


def file_hash(data: bytes) -> str:
    """SHA-256 of the uploaded bytes — the duplicate-detection key."""
    return hashlib.sha256(data).hexdigest()


async def find_confirmed_by_hash(
    session: AsyncSession, *, user_id: int, content_hash: str
) -> LabReport | None:
    """A user's already-CONFIRMED report with the same file bytes, if any. Only confirmed
    reports count as a duplicate — a discarded/deleted upload should not block re-uploading."""
    report: LabReport | None = await session.scalar(
        select(LabReport)
        .where(
            LabReport.user_id == user_id,
            LabReport.content_hash == content_hash,
            LabReport.status == ReportStatus.CONFIRMED,
        )
        .order_by(LabReport.id)
        .limit(1)
    )
    return report


async def persist_confirmed(
    session: AsyncSession,
    *,
    report: LabReport,
    analytes: list[ExtractedAnalyte],
    report_date: date | None,
    lab: str | None,
    birth_date: date | None = None,
    sex: str | None = None,
    conclusion: str | None = None,
    report_type: str | None = None,
    narrative: str | None = None,
) -> list[LabResult]:
    """Write confirmed LabResult rows and mark the report CONFIRMED.

    Called only after the user has confirmed the values (rail #2). The numeric flag and
    the attention ``flagged`` mark are computed deterministically here, never by the model
    (the model only reports the lab's own out-of-range indicator, which feeds ``flagged``).
    A narrative document (``narrative`` set, no analytes) is stored as kind=NARRATIVE.
    """
    report.report_date = report_date
    report.birth_date = birth_date or report.birth_date  # keep an already-known DOB
    report.sex = sex or report.sex  # keep an already-known sex
    report.lab = normalize_lab(lab)
    report.conclusion = conclusion
    report.report_type = report_type
    report.narrative = narrative
    report.kind = ReportKind.NARRATIVE if (narrative and not analytes) else ReportKind.TABULAR
    report.status = ReportStatus.CONFIRMED

    results: list[LabResult] = []
    for a in analytes:
        result = LabResult(
            report_id=report.id,
            analyte=a.analyte,
            value=a.value,
            value_text=a.value_text,
            unit=a.unit,
            ref_low=a.ref_low,
            ref_high=a.ref_high,
            ref_text=a.ref_text,
            flag=classify(a.value, a.value_text, a.ref_low, a.ref_high, a.ref_text),
            flagged=is_out_of_range(a.value, a.ref_low, a.ref_high, a.out_of_range, a.value_text),
            section=a.section,
        )
        session.add(result)
        results.append(result)
    await session.flush()
    return results
