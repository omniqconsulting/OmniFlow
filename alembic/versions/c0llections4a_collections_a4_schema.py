"""collections_a4_schema

Revision ID: c0llections4a
Revises: c0llections3a
Create Date: 2026-07-16 02:00:00.000000

Workstream A, Phase A4 — Escalation Dashboard & Reporting UI. Adds:
  - customers.collections_outstanding_amount — per-case amount, source of
    truth for the dashboard rollup (Req #17: total outstanding / total
    overdue).
  - customers.collections_payment_status — PENDING / PARTIAL / COMPLETED,
    clearly surfaced per Req #16.
Invoice/statement/receipt uploads (Req #19) reuse the existing polymorphic
MediaUpload table (entity_type="collections_document") — no schema change
needed there. Auto-generated daily follow-up tasks (Req #18) reuse the
existing Ticket table (ticket_type="D") — no schema change needed there
either. Idempotent, same pattern as prior Collections migrations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0llections4a'
down_revision: Union[str, Sequence[str], None] = 'c0llections3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    customers_cols = {c['name'] for c in inspector.get_columns('customers')}

    with op.batch_alter_table('customers', schema=None) as batch_op:
        if 'collections_outstanding_amount' not in customers_cols:
            batch_op.add_column(sa.Column('collections_outstanding_amount', sa.Float(), nullable=True))
        if 'collections_payment_status' not in customers_cols:
            batch_op.add_column(sa.Column('collections_payment_status', sa.String(), nullable=True, server_default='PENDING'))

    op.execute("UPDATE customers SET collections_payment_status = 'PENDING' WHERE collections_payment_status IS NULL AND open_balance_lock = TRUE")


def downgrade() -> None:
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_column('collections_payment_status')
        batch_op.drop_column('collections_outstanding_amount')
