"""condition DISMISSED status — remember an AI-proposed finding the user waved off

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-26 09:00:00.000000

Widens conditions.status to admit the new ``DISMISSED`` member (the agent-proposed
problem the user chose not to track). SQLite stores the enum as VARCHAR and the column
carries no CHECK constraint, so this is a length-only recreate via batch mode.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW = sa.Enum("ACTIVE", "RESOLVED", "DISMISSED", name="conditionstatus")
_OLD = sa.Enum("ACTIVE", "RESOLVED", name="conditionstatus")


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("conditions", schema=None) as batch_op:
        batch_op.alter_column("status", existing_type=_OLD, type_=_NEW, existing_nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("conditions", schema=None) as batch_op:
        batch_op.alter_column("status", existing_type=_NEW, type_=_OLD, existing_nullable=False)
