"""tier 1.1: condition status + review timestamp + reminder->medication link

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-19 14:24:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('conditions', schema=None) as batch_op:
        # server_default so the NOT NULL column backfills existing rows as ACTIVE.
        batch_op.add_column(
            sa.Column(
                'status',
                sa.Enum('ACTIVE', 'RESOLVED', name='conditionstatus'),
                nullable=False,
                server_default='ACTIVE',
            )
        )
        batch_op.add_column(sa.Column('last_review_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('reminders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('medication_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_reminders_medication_id', 'medications', ['medication_id'], ['id'], ondelete='CASCADE'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('reminders', schema=None) as batch_op:
        batch_op.drop_constraint('fk_reminders_medication_id', type_='foreignkey')
        batch_op.drop_column('medication_id')

    with op.batch_alter_table('conditions', schema=None) as batch_op:
        batch_op.drop_column('last_review_at')
        batch_op.drop_column('status')
