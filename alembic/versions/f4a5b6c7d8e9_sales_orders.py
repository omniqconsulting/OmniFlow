"""sales_orders

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, Sequence[str], None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "sales_orders" not in existing_tables:
        op.create_table(
            "sales_orders",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("display_id", sa.String(), nullable=True),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("agent_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("status", sa.String(), nullable=True, server_default="DRAFT"),
            sa.Column("payment_terms", sa.String(), nullable=True),
            sa.Column("delivery_address", sa.Text(), nullable=True),
            sa.Column("expected_delivery_date", sa.Date(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("call_log_id", sa.String(), nullable=True),
            sa.Column("price_list_id_snapshot", sa.String(), nullable=True),
            sa.Column("total_amount", sa.Float(), nullable=True, server_default="0"),
            sa.Column("total_cost", sa.Float(), nullable=True, server_default="0"),
            sa.Column("gross_margin_pct", sa.Float(), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("dispatched_at", sa.DateTime(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.Column("cancellation_reason", sa.Text(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "sales_order_items" not in existing_tables:
        op.create_table(
            "sales_order_items",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("order_id", sa.String(), sa.ForeignKey("sales_orders.id"), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("qty_ordered", sa.Float(), nullable=False),
            sa.Column("unit_id", sa.String(), sa.ForeignKey("units_of_measure.id"), nullable=True),
            sa.Column("unit_price", sa.Float(), nullable=False),
            sa.Column("price_source", sa.String(), nullable=True),
            sa.Column("manual_override_price", sa.Float(), nullable=True),
            sa.Column("override_reason", sa.Text(), nullable=True),
            sa.Column("approval_status", sa.String(), nullable=True),
            sa.Column("cost_snapshot", sa.Float(), nullable=True),
            sa.Column("qty_dispatched", sa.Float(), nullable=True, server_default="0"),
            sa.Column("line_total", sa.Float(), nullable=True, server_default="0"),
            sa.Column("stock_status", sa.String(), nullable=True),
            sa.Column("in_transit_arrival", sa.Date(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "stock_reservations" not in existing_tables:
        op.create_table(
            "stock_reservations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("order_id", sa.String(), sa.ForeignKey("sales_orders.id"), nullable=False),
            sa.Column("order_item_id", sa.String(), sa.ForeignKey("sales_order_items.id"), nullable=True),
            sa.Column("qty_reserved", sa.Float(), nullable=False),
            sa.Column("status", sa.String(), nullable=True, server_default="ACTIVE"),
            sa.Column("reserved_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("reserved_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("fulfilled_at", sa.DateTime(), nullable=True),
            sa.Column("released_at", sa.DateTime(), nullable=True),
            sa.Column("release_reason", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # FK constraint on crm_call_logs.order_id (column added in Brief 04 migration)
    if "crm_call_logs" in existing_tables:
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("crm_call_logs")}
        if "fk_calllog_order" not in existing_fks:
            try:
                op.create_foreign_key(
                    "fk_calllog_order", "crm_call_logs", "sales_orders",
                    ["order_id"], ["id"],
                )
            except Exception:
                pass

    inspector = sa.inspect(bind)  # refresh after table creation
    so_indexes = {ix["name"] for ix in inspector.get_indexes("sales_orders")}
    if "idx_sales_orders_tenant_status" not in so_indexes:
        op.create_index("idx_sales_orders_tenant_status", "sales_orders", ["tenant_id", "status"])
    if "idx_sales_orders_customer" not in so_indexes:
        op.create_index("idx_sales_orders_customer", "sales_orders", ["customer_id", "created_at"])

    sr_indexes = {ix["name"] for ix in inspector.get_indexes("stock_reservations")}
    if "idx_reservations_order" not in sr_indexes:
        op.create_index("idx_reservations_order", "stock_reservations", ["order_id", "status"])
    if "idx_reservations_product" not in sr_indexes:
        op.create_index("idx_reservations_product", "stock_reservations", ["product_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "crm_call_logs" in existing_tables:
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("crm_call_logs")}
        if "fk_calllog_order" in existing_fks:
            op.drop_constraint("fk_calllog_order", "crm_call_logs", type_="foreignkey")
    if "stock_reservations" in existing_tables:
        op.drop_table("stock_reservations")
    if "sales_order_items" in existing_tables:
        op.drop_table("sales_order_items")
    if "sales_orders" in existing_tables:
        op.drop_table("sales_orders")
