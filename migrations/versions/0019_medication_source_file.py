"""medication source_file — keep the prescription photo/PDF a med was read from

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-27 13:30:00.000000

Adds a nullable ``medications.source_file`` (path on disk) so a medication created from a
prescription photo links back to its original image — the user can re-open it (a 📄 button on
the med card), exactly like a lab report's ``source_file``. NULL for a manually-entered med.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("medications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_file", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("medications", schema=None) as batch_op:
        batch_op.drop_column("source_file")
