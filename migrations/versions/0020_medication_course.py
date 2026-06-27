"""medication course — group meds from one prescription under a label

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-27 14:00:00.000000

Adds a nullable ``medications.course`` (a group label, e.g. "Рецепт від уролога") so the meds
read from one prescription are grouped together in the 💊 Мої ліки list and on the card. NULL
for a standalone manually-entered medication.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("medications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("course", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("medications", schema=None) as batch_op:
        batch_op.drop_column("course")
