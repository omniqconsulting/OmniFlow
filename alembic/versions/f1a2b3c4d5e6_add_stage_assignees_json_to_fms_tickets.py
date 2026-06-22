"""add stage_assignees_json to fms_tickets

Revision ID: f1a2b3c4d5e6
Revises: 65eca3bf0268
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = '65eca3bf0268'
branch_labels = None
depends_on = None


def _col_exists(table, column):
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return column in [c['name'] for c in insp.get_columns(table)]


def upgrade():
    if not _col_exists('fms_tickets', 'stage_assignees_json'):
        op.add_column('fms_tickets', sa.Column('stage_assignees_json', sa.Text(), nullable=True))


def downgrade():
    if _col_exists('fms_tickets', 'stage_assignees_json'):
        op.drop_column('fms_tickets', 'stage_assignees_json')
