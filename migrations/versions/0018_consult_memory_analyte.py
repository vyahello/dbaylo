"""consult_memory analyte anchor — group a trend-chart conversation by its indicator

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-26 10:00:00.000000

A consultation about one indicator's TREND chart spans many reports, so it had no single
report anchor and was dumped into the general ("Загальні розмови") bucket. Two nullable
columns let it be grouped + labelled by the analyte instead: ``analyte_key`` (the
cross-report series key) and ``subject_label`` (its display name, captured at write time).
Both NULL for report/section consults and for legacy rows (which therefore stay where they
were — the fix applies to consultations recorded from now on).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0018'
down_revision: Union[str, Sequence[str], None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('consult_memory', sa.Column('analyte_key', sa.String(), nullable=True))
    op.add_column('consult_memory', sa.Column('subject_label', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('consult_memory', 'subject_label')
    op.drop_column('consult_memory', 'analyte_key')
