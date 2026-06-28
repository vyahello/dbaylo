"""user city — remember the user's city for price/clinic search

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-28 12:00:00.000000

Adds a nullable ``users.city`` so the city is asked ONCE and reused (medicine-price search and the
clinic finder), instead of being re-asked every session. NULL until the user names their city.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("city", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("city")
