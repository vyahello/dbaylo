"""medication until — the last day to take a med (auto-expire the course)

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-27 14:40:00.000000

Adds a nullable ``medications.until`` (DATE) — the last day to take the med, derived from the
prescription's printed duration ("3 міс." → today + 3 months). After it the scheduler retires the
med's reminders (the course is over) and it leaves the lists. NULL = open-ended (never expires).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("medications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("until", sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("medications", schema=None) as batch_op:
        batch_op.drop_column("until")
