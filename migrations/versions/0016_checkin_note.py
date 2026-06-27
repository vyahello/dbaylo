"""checkin note — remember the user's own words for state memory across check-ins

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-25 17:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("check_ins", schema=None) as batch_op:
        batch_op.add_column(sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("check_ins", schema=None) as batch_op:
        batch_op.drop_column("note")
