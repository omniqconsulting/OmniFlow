"""fix stage_schedule_json missing on postgres

Revision ID: c4d5e6f7a8b9
Revises: a2b3c4d5e6f7
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4d5e6f7a8b9'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text(
            "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS stage_schedule_json TEXT"
        ))
        bind.execute(sa.text(
            "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS planned_start TIMESTAMP"
        ))
        bind.execute(sa.text(
            "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS planned_end TIMESTAMP"
        ))
    else:
        for tbl, col, typ in [
            ('fms_tickets',       'stage_schedule_json', sa.Text()),
            ('fms_stage_history', 'planned_start',        sa.DateTime()),
            ('fms_stage_history', 'planned_end',          sa.DateTime()),
        ]:
            try:
                op.add_column(tbl, sa.Column(col, typ, nullable=True))
            except Exception:
                pass


def downgrade():
    pass
