"""add_tenant_notification_office_hours

Revision ID: 05875ebc7c8b
Revises: e1f2a3b4c5d6
Create Date: 2026-06-26 20:42:18.751678

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '05875ebc7c8b'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table, col_name, col_type, **kwargs):
    """Add column only if it doesn't already exist (SQLite-safe)."""
    from sqlalchemy import inspect, text
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = [c['name'] for c in inspector.get_columns(table)]
    if col_name not in existing:
        op.add_column(table, sa.Column(col_name, col_type, **kwargs))


def upgrade() -> None:
    """E-15: Add office hours and notification config columns to tenants."""
    _add_column_if_missing('tenants', 'work_start_time',       sa.String(), nullable=True)
    _add_column_if_missing('tenants', 'work_end_time',         sa.String(), nullable=True)
    _add_column_if_missing('tenants', 'work_days',             sa.String(), nullable=True)
    _add_column_if_missing('tenants', 'timezone',              sa.String(), nullable=True)
    _add_column_if_missing('tenants', 'suppress_notif_outside_hours', sa.Boolean(), server_default='0')
    _add_column_if_missing('tenants', 'checklist_remind_before_hours', sa.Integer(), server_default='2')
    _add_column_if_missing('tenants', 'checklist_remind_repeat_hours', sa.Integer(), server_default='4')
    _add_column_if_missing('tenants', 'ticket_notif_on_assign', sa.Boolean(), server_default='1')
    _add_column_if_missing('tenants', 'ticket_notif_unack_hours', sa.Integer(), server_default='4')
    _add_column_if_missing('tenants', 'ticket_notif_tat_pct',   sa.Integer(), server_default='80')
    _add_column_if_missing('tenants', 'ticket_notif_tat_pct_both', sa.Integer(), server_default='90')
    _add_column_if_missing('tenants', 'fms_notif_on_stage_entry', sa.Boolean(), server_default='1')
    _add_column_if_missing('tenants', 'fms_notif_tat_pct',     sa.Integer(), server_default='80')
    _add_column_if_missing('tenants', 'fms_notif_on_backward', sa.Boolean(), server_default='1')
    _add_column_if_missing('tenants', 'fms_notif_on_flag',     sa.Boolean(), server_default='1')


def downgrade() -> None:
    """Downgrade is a no-op for SQLite (column drops not supported)."""
    pass
