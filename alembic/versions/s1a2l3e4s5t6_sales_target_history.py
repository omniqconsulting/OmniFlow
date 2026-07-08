"""sales_target_history

Revision ID: s1a2l3e4s5t6
Revises: p2e3n4d5i6n7
Create Date: 2026-07-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 's1a2l3e4s5t6'
down_revision: Union[str, Sequence[str], None] = 'p2e3n4d5i6n7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "sales_target_history" not in existing_tables:
        op.create_table(
            "sales_target_history",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("agent_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("period_label", sa.String(), nullable=False),
            sa.Column("old_target_amount", sa.Float(), nullable=True),
            sa.Column("new_target_amount", sa.Float(), nullable=False),
            sa.Column("old_target_orders", sa.Integer(), nullable=True),
            sa.Column("new_target_orders", sa.Integer(), nullable=True),
            sa.Column("changed_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("changed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    sth_indexes = {ix["name"] for ix in inspector.get_indexes("sales_target_history")}
    if "idx_sales_target_history_agent_period" not in sth_indexes:
        op.create_index("idx_sales_target_history_agent_period", "sales_target_history",
                         ["tenant_id", "agent_id", "period_label"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "sales_target_history" in existing_tables:
        op.drop_table("sales_target_history")
