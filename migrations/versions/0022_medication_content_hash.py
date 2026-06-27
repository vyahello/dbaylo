"""medication content_hash — dedup a re-dropped prescription photo

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-27 17:00:00.000000

Adds a nullable ``medications.content_hash`` (SHA-256 of the prescription photo's bytes), the same
duplicate-detection key labs use — so dropping the same script twice points to the existing course
instead of creating a second one. NULL for a manually-entered medication.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("medications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("content_hash", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("medications", schema=None) as batch_op:
        batch_op.drop_column("content_hash")
