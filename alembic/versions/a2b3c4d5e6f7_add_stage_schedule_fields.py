"""add stage schedule fields

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'a2b3c4d5e6f7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    # planned_start and planned_end on fms_stage_history
    # stage_schedule_json on fms_tickets was skipped here (added by c4d5e6f7a8b9)
    try:
        op.add_column('fms_stage_history',
            sa.Column('planned_start', sa.DateTime(), nullable=True))
    except Exception:
        pass
    try:
        op.add_column('fms_stage_history',
            sa.Column('planned_end', sa.DateTime(), nullable=True))
    except Exception:
        pass


def downgrade():
    try:
        op.drop_column('fms_stage_history', 'planned_end')
    except Exception:
        pass
    try:
        op.drop_column('fms_stage_history', 'planned_start')
    except Exception:
        pass
