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


def _col_exists(table, column):
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return column in [c['name'] for c in insp.get_columns(table)]


def upgrade():
    # stage_schedule_json may already exist on SQLite (added manually during dev)
    if not _col_exists('fms_tickets', 'stage_schedule_json'):
        op.add_column('fms_tickets',
            sa.Column('stage_schedule_json', sa.Text(), nullable=True))
    if not _col_exists('fms_stage_history', 'planned_start'):
        op.add_column('fms_stage_history',
            sa.Column('planned_start', sa.DateTime(), nullable=True))
    if not _col_exists('fms_stage_history', 'planned_end'):
        op.add_column('fms_stage_history',
            sa.Column('planned_end', sa.DateTime(), nullable=True))


def downgrade():
    if _col_exists('fms_stage_history', 'planned_end'):
        op.drop_column('fms_stage_history', 'planned_end')
    if _col_exists('fms_stage_history', 'planned_start'):
        op.drop_column('fms_stage_history', 'planned_start')
    if _col_exists('fms_tickets', 'stage_schedule_json'):
        op.drop_column('fms_tickets', 'stage_schedule_json')
