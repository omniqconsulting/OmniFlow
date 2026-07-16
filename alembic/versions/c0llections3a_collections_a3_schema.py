"""collections_a3_schema

Revision ID: c0llections3a
Revises: c0llections2a
Create Date: 2026-07-16 01:00:00.000000

Workstream A, Phase A3 — Automated Notifications. Adds dedup markers so the
new daily collections-notification job (app/collections_notify.py) doesn't
re-fire the same tiered-escalation (Req #11) or non-responsive-party (Req #14)
alert on every run:
  - customers.collections_last_tier_notified — highest day-tier already
    notified to the owner for this case.
  - customers.collections_non_responsive_alerted — set once the one-time
    non-responsive alert has fired for this case.
Idempotent (inspect-then-add-column), same pattern as prior Collections
migrations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0llections3a'
down_revision: Union[str, Sequence[str], None] = 'c0llections2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    customers_cols = {c['name'] for c in inspector.get_columns('customers')}

    with op.batch_alter_table('customers', schema=None) as batch_op:
        if 'collections_last_tier_notified' not in customers_cols:
            batch_op.add_column(sa.Column('collections_last_tier_notified', sa.Integer(), nullable=True))
        if 'collections_non_responsive_alerted' not in customers_cols:
            batch_op.add_column(sa.Column('collections_non_responsive_alerted', sa.Boolean(), nullable=True, server_default=sa.false()))

    op.execute("UPDATE customers SET collections_non_responsive_alerted = FALSE WHERE collections_non_responsive_alerted IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_column('collections_non_responsive_alerted')
        batch_op.drop_column('collections_last_tier_notified')
