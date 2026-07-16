"""collections_a2_schema

Revision ID: c0llections2a
Revises: c0llections1a
Create Date: 2026-07-16 00:30:00.000000

Workstream A, Phase A2 — Call Cap & Auto-Escalation Logic. Adds the fields
needed to evaluate the cap and surface a case in the Escalation section:
  - customers.collections_case_due_date — due-date basis for day-tier
    (30/60/90) filtering (Req #11).
  - customers.collections_escalated / collections_escalated_at — auto-set
    once collections_call_attempt_count (added in A1) reaches the tenant's
    collections_call_attempt_cap (added in A1).
Idempotent (inspect-then-add-column), same pattern as c0llections1a.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0llections2a'
down_revision: Union[str, Sequence[str], None] = 'c0llections1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    customers_cols = {c['name'] for c in inspector.get_columns('customers')}

    with op.batch_alter_table('customers', schema=None) as batch_op:
        if 'collections_case_due_date' not in customers_cols:
            batch_op.add_column(sa.Column('collections_case_due_date', sa.Date(), nullable=True))
        if 'collections_escalated' not in customers_cols:
            batch_op.add_column(sa.Column('collections_escalated', sa.Boolean(), nullable=True, server_default=sa.false()))
        if 'collections_escalated_at' not in customers_cols:
            batch_op.add_column(sa.Column('collections_escalated_at', sa.DateTime(), nullable=True))

    op.execute("UPDATE customers SET collections_escalated = FALSE WHERE collections_escalated IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_column('collections_escalated_at')
        batch_op.drop_column('collections_escalated')
        batch_op.drop_column('collections_case_due_date')
