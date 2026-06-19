"""tier 1.2: link a condition / repeat-lab reminder back to its originating report

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-19 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('conditions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('report_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_conditions_report_id', 'lab_reports', ['report_id'], ['id'], ondelete='SET NULL'
        )

    with op.batch_alter_table('reminders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('report_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_reminders_report_id', 'lab_reports', ['report_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('reminders', schema=None) as batch_op:
        batch_op.drop_constraint('fk_reminders_report_id', type_='foreignkey')
        batch_op.drop_column('report_id')

    with op.batch_alter_table('conditions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_conditions_report_id', type_='foreignkey')
        batch_op.drop_column('report_id')
