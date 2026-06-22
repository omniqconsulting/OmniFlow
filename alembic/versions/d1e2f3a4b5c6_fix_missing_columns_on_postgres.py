"""fix missing columns on postgres: stage_assignees_json, custom_fields_json, custom_fields_data_json

Revision ID: d1e2f3a4b5c6
Revises: c4d5e6f7a8b9
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'd1e2f3a4b5c6'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text(
            "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS stage_assignees_json TEXT"
        ))
        bind.execute(sa.text(
            "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS custom_fields_json TEXT DEFAULT '[]'"
        ))
        bind.execute(sa.text(
            "ALTER TABLE library_flow_stages ADD COLUMN IF NOT EXISTS custom_fields_json TEXT DEFAULT '[]'"
        ))
        bind.execute(sa.text(
            "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS custom_fields_data_json TEXT"
        ))
    else:
        # SQLite — columns already exist in local dev; skip to avoid errors
        pass


def downgrade():
    pass
