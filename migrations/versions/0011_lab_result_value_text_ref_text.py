"""lab_results.value_text + ref_text: keep qualitative values and the printed reference

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-21 21:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("lab_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("value_text", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("ref_text", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("lab_results", schema=None) as batch_op:
        batch_op.drop_column("ref_text")
        batch_op.drop_column("value_text")
