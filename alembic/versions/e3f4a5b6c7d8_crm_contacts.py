"""crm_contacts

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    existing_customer_cols = {c["name"] for c in inspector.get_columns("customers")} if "customers" in existing_tables else set()

    if "customers" in existing_tables:
        if "assigned_agent_id" not in existing_customer_cols:
            op.add_column("customers", sa.Column("assigned_agent_id", sa.String(), sa.ForeignKey("users.id"), nullable=True))
        if "customer_tier" not in existing_customer_cols:
            op.add_column("customers", sa.Column("customer_tier", sa.String(), nullable=True, server_default="UNRANKED"))
        if "last_contacted_at" not in existing_customer_cols:
            op.add_column("customers", sa.Column("last_contacted_at", sa.DateTime(), nullable=True))
        if "contact_freq_days" not in existing_customer_cols:
            op.add_column("customers", sa.Column("contact_freq_days", sa.Integer(), nullable=True, server_default="30"))
        if "price_list_id" not in existing_customer_cols:
            op.add_column("customers", sa.Column("price_list_id", sa.String(), nullable=True))
        if "gstin" not in existing_customer_cols:
            op.add_column("customers", sa.Column("gstin", sa.String(), nullable=True))
        if "credit_limit" not in existing_customer_cols:
            op.add_column("customers", sa.Column("credit_limit", sa.Float(), nullable=True))
        if "billing_address" not in existing_customer_cols:
            op.add_column("customers", sa.Column("billing_address", sa.Text(), nullable=True))
        if "shipping_address" not in existing_customer_cols:
            op.add_column("customers", sa.Column("shipping_address", sa.Text(), nullable=True))

    if "crm_call_logs" not in existing_tables:
        op.create_table(
            "crm_call_logs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("agent_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("contacted_at", sa.DateTime(), nullable=True),
            sa.Column("outcome", sa.String(), nullable=False),
            sa.Column("follow_up_at", sa.DateTime(), nullable=True),
            sa.Column("follow_up_done", sa.Boolean(), nullable=True),
            sa.Column("order_id", sa.String(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "idx_call_logs_agent_followup",
            "crm_call_logs",
            ["agent_id", "follow_up_at", "follow_up_done"],
            postgresql_where=sa.text("follow_up_done = FALSE"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_call_logs" in inspector.get_table_names():
        op.drop_table("crm_call_logs")
    if "customers" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("customers")}
        for col in ["assigned_agent_id", "customer_tier", "last_contacted_at", "contact_freq_days",
                    "price_list_id", "gstin", "credit_limit", "billing_address", "shipping_address"]:
            if col in cols:
                op.drop_column("customers", col)
