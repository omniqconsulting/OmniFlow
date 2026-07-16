"""attendance_b4_schema

Revision ID: att3nd4nc4b
Revises: att3nd4nc1b
Create Date: 2026-07-16 04:00:00.000000

Workstream B, Phase B4 — Reconciliation & Forward Compatibility. Adds
is_half_day to attendance_records (manual override for a late-arrival/
early-leave day) and leave_requests (half-day leave), so a future payroll
consumer can read a full present/absent/leave/half-day status per day
without any further schema change. No payroll calculation logic is added —
structure only, per the standing scope rule for this phase.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'att3nd4nc4b'
down_revision: Union[str, Sequence[str], None] = 'att3nd4nc1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    attendance_cols = {c['name'] for c in inspector.get_columns('attendance_records')}
    with op.batch_alter_table('attendance_records', schema=None) as batch_op:
        if 'is_half_day' not in attendance_cols:
            batch_op.add_column(sa.Column('is_half_day', sa.Boolean(), nullable=True, server_default=sa.false()))

    leave_cols = {c['name'] for c in inspector.get_columns('leave_requests')}
    with op.batch_alter_table('leave_requests', schema=None) as batch_op:
        if 'is_half_day' not in leave_cols:
            batch_op.add_column(sa.Column('is_half_day', sa.Boolean(), nullable=True, server_default=sa.false()))

    op.execute("UPDATE attendance_records SET is_half_day = FALSE WHERE is_half_day IS NULL")
    op.execute("UPDATE leave_requests SET is_half_day = FALSE WHERE is_half_day IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('leave_requests', schema=None) as batch_op:
        batch_op.drop_column('is_half_day')
    with op.batch_alter_table('attendance_records', schema=None) as batch_op:
        batch_op.drop_column('is_half_day')
