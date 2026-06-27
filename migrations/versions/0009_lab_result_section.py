"""lab_results.section: panel grouping (blood vs urine)

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-20 14:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("lab_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("section", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("lab_results", schema=None) as batch_op:
        batch_op.drop_column("section")
