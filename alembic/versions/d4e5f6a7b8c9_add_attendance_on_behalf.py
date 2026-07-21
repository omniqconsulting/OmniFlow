"""add attendance on-behalf recording columns

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('attendance_records') as batch_op:
        batch_op.add_column(sa.Column('recorded_by_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('on_behalf_reason', sa.Text(), nullable=True))
    with op.batch_alter_table('attendance_records') as batch_op:
        batch_op.create_foreign_key(
            'fk_attendance_records_recorded_by_id', 'users', ['recorded_by_id'], ['id'],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('attendance_records') as batch_op:
        batch_op.drop_constraint('fk_attendance_records_recorded_by_id', type_='foreignkey')
        batch_op.drop_column('on_behalf_reason')
        batch_op.drop_column('recorded_by_id')
