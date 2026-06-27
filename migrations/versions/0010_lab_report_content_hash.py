"""lab_reports.content_hash: detect duplicate uploads by file hash

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-20 20:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("lab_reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("content_hash", sa.String(), nullable=True))
        batch_op.create_index(batch_op.f("ix_lab_reports_content_hash"), ["content_hash"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("lab_reports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_lab_reports_content_hash"))
        batch_op.drop_column("content_hash")
