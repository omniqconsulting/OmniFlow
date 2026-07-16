"""collections_a1_schema

Revision ID: c0llections1a
Revises: wa9ev1nttggl
Create Date: 2026-07-16 00:00:00.000000

Workstream A (Collections & Escalation Engine), Phase A1 — schema only, fully
inert until the tenant is opted into COLLECTIONS_MODULE via FEATURE_CATALOG /
TenantFeatureOverride (see app/constants.py). Adds:
  - tenants.collections_call_attempt_cap / collections_escalation_tiers /
    collections_channel_{sms,whatsapp,email}_enabled /
    collections_owner_notify_enabled — Setup > Collections config panel.
  - customers.open_balance_lock (Req #1: blocks duplicate party entry while a
    payment is outstanding) and customers.collections_call_attempt_count
    (per-open-case call counter, enforced in A2).
Idempotent (inspect-then-add-column), same pattern as wa9ev1nttggl, since this
app's own startup self-heal can create these columns from the models before
Alembic ever runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0llections1a'
down_revision: Union[str, Sequence[str], None] = 'wa9ev1nttggl'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tenants_cols = {c['name'] for c in inspector.get_columns('tenants')}
    customers_cols = {c['name'] for c in inspector.get_columns('customers')}

    with op.batch_alter_table('tenants', schema=None) as batch_op:
        if 'collections_call_attempt_cap' not in tenants_cols:
            batch_op.add_column(sa.Column('collections_call_attempt_cap', sa.Integer(), nullable=True, server_default='2'))
        if 'collections_escalation_tiers' not in tenants_cols:
            batch_op.add_column(sa.Column('collections_escalation_tiers', sa.String(), nullable=True, server_default='30,60,90'))
        if 'collections_channel_sms_enabled' not in tenants_cols:
            batch_op.add_column(sa.Column('collections_channel_sms_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))
        if 'collections_channel_whatsapp_enabled' not in tenants_cols:
            batch_op.add_column(sa.Column('collections_channel_whatsapp_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))
        if 'collections_channel_email_enabled' not in tenants_cols:
            batch_op.add_column(sa.Column('collections_channel_email_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))
        if 'collections_owner_notify_enabled' not in tenants_cols:
            batch_op.add_column(sa.Column('collections_owner_notify_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))

    with op.batch_alter_table('customers', schema=None) as batch_op:
        if 'open_balance_lock' not in customers_cols:
            batch_op.add_column(sa.Column('open_balance_lock', sa.Boolean(), nullable=True, server_default=sa.false()))
        if 'collections_call_attempt_count' not in customers_cols:
            batch_op.add_column(sa.Column('collections_call_attempt_count', sa.Integer(), nullable=True, server_default='0'))

    op.execute("UPDATE tenants SET collections_call_attempt_cap = 2 WHERE collections_call_attempt_cap IS NULL")
    op.execute("UPDATE tenants SET collections_escalation_tiers = '30,60,90' WHERE collections_escalation_tiers IS NULL")
    op.execute("UPDATE tenants SET collections_channel_sms_enabled = FALSE WHERE collections_channel_sms_enabled IS NULL")
    op.execute("UPDATE tenants SET collections_channel_whatsapp_enabled = FALSE WHERE collections_channel_whatsapp_enabled IS NULL")
    op.execute("UPDATE tenants SET collections_channel_email_enabled = FALSE WHERE collections_channel_email_enabled IS NULL")
    op.execute("UPDATE tenants SET collections_owner_notify_enabled = FALSE WHERE collections_owner_notify_enabled IS NULL")
    op.execute("UPDATE customers SET open_balance_lock = FALSE WHERE open_balance_lock IS NULL")
    op.execute("UPDATE customers SET collections_call_attempt_count = 0 WHERE collections_call_attempt_count IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_column('collections_call_attempt_count')
        batch_op.drop_column('open_balance_lock')
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.drop_column('collections_owner_notify_enabled')
        batch_op.drop_column('collections_channel_email_enabled')
        batch_op.drop_column('collections_channel_whatsapp_enabled')
        batch_op.drop_column('collections_channel_sms_enabled')
        batch_op.drop_column('collections_escalation_tiers')
        batch_op.drop_column('collections_call_attempt_cap')
