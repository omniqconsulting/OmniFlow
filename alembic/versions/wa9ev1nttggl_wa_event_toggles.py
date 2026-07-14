"""wa_event_toggles

Revision ID: wa9ev1nttggl
Revises: d84d69ded1cc
Create Date: 2026-07-14 12:00:00.000000

Adds per-event WhatsApp channel toggles on tenants (Setup > Notifications >
WhatsApp Notifications) — these gate the WhatsApp send for each pipeline
independently of the in-app notification toggles added in prior E-15
migrations. Also adds users.whatsapp_notifications_enabled, an employee's
own on/off preference distinct from whatsapp_opt_in_status (verification) —
lets a verified employee mute WhatsApp sends for themselves. Idempotent,
same pattern as d84d69ded1cc, since this app's own startup self-heal can
create these columns from the models before Alembic ever runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'wa9ev1nttggl'
down_revision: Union[str, Sequence[str], None] = 'd84d69ded1cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TENANT_COLUMNS = [
    'wa_notif_ticket_assigned',
    'wa_notif_ticket_escalated',
    'wa_notif_fms_ticket_created',
    'wa_notif_fms_stage_transition',
    'wa_notif_order_placed',
    'wa_notif_order_dispatched',
    'wa_notif_ticket_closed',
    'wa_notif_ticket_tat_reminder',
    'wa_notif_fms_ticket_closed',
    'wa_notif_fms_ticket_flagged',
    'wa_notif_po_placed',
    'wa_notif_po_accepted',
]

_NEW_USER_COLUMNS = [
    'whatsapp_notifications_enabled',
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tenants_cols = {c['name'] for c in inspector.get_columns('tenants')}
    users_cols = {c['name'] for c in inspector.get_columns('users')}

    with op.batch_alter_table('tenants', schema=None) as batch_op:
        for col in _NEW_TENANT_COLUMNS:
            if col not in tenants_cols:
                batch_op.add_column(sa.Column(col, sa.Boolean(), nullable=True, server_default=sa.true()))

    with op.batch_alter_table('users', schema=None) as batch_op:
        for col in _NEW_USER_COLUMNS:
            if col not in users_cols:
                batch_op.add_column(sa.Column(col, sa.Boolean(), nullable=True, server_default=sa.true()))

    # Backfill any existing rows so the toggle reads as "on" by default.
    for col in _NEW_TENANT_COLUMNS:
        op.execute(f"UPDATE tenants SET {col} = TRUE WHERE {col} IS NULL")
    for col in _NEW_USER_COLUMNS:
        op.execute(f"UPDATE users SET {col} = TRUE WHERE {col} IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        for col in reversed(_NEW_USER_COLUMNS):
            batch_op.drop_column(col)
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        for col in reversed(_NEW_TENANT_COLUMNS):
            batch_op.drop_column(col)
