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
    # stage_schedule_json on fms_tickets was already added by a prior partial migration
    op.add_column('fms_stage_history',
        sa.Column('planned_start', sa.DateTime(), nullable=True))
    op.add_column('fms_stage_history',
        sa.Column('planned_end', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('fms_stage_history', 'planned_end')
    op.drop_column('fms_stage_history', 'planned_start')
