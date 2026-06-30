"""pricing_margin

Revision ID: p1r2i3c4e5m6
Revises: f4a5b6c7d8e9
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'p1r2i3c4e5m6'
down_revision: Union[str, Sequence[str], None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "price_lists" not in existing_tables:
        op.create_table(
            "price_lists",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_default", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("valid_from", sa.Date(), nullable=True),
            sa.Column("valid_to", sa.Date(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("created_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "price_list_items" not in existing_tables:
        op.create_table(
            "price_list_items",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("price_list_id", sa.String(), sa.ForeignKey("price_lists.id"), nullable=False),
            sa.Column("product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("unit_price", sa.Float(), nullable=False),
            sa.Column("min_qty", sa.Float(), nullable=True, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "price_list_item_history" not in existing_tables:
        op.create_table(
            "price_list_item_history",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("price_list_id", sa.String(), sa.ForeignKey("price_lists.id"), nullable=False),
            sa.Column("price_list_name_snapshot", sa.String(), nullable=True),
            sa.Column("product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("old_price", sa.Float(), nullable=True),
            sa.Column("new_price", sa.Float(), nullable=False),
            sa.Column("changed_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("changed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "customer_price_overrides" not in existing_tables:
        op.create_table(
            "customer_price_overrides",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("unit_price", sa.Float(), nullable=False),
            sa.Column("valid_from", sa.Date(), nullable=True),
            sa.Column("valid_to", sa.Date(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "cost_entries" not in existing_tables:
        op.create_table(
            "cost_entries",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("cost_type", sa.String(), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("actor_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # FK constraint on customers.price_list_id (column added in Brief 04/05 without FK)
    if "customers" in existing_tables:
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("customers")}
        if "fk_customer_price_list" not in existing_fks:
            try:
                op.create_foreign_key(
                    "fk_customer_price_list", "customers", "price_lists",
                    ["price_list_id"], ["id"],
                )
            except Exception:
                pass

    inspector = sa.inspect(bind)  # refresh after table creation
    ce_indexes = {ix["name"] for ix in inspector.get_indexes("cost_entries")}
    if "idx_cost_entries_product" not in ce_indexes:
        op.create_index("idx_cost_entries_product", "cost_entries", ["product_id", "effective_date"])

    pli_indexes = {ix["name"] for ix in inspector.get_indexes("price_list_items")}
    if "idx_price_list_items_product" not in pli_indexes:
        op.create_index("idx_price_list_items_product", "price_list_items", ["product_id", "price_list_id"])

    cpo_indexes = {ix["name"] for ix in inspector.get_indexes("customer_price_overrides")}
    if "idx_customer_price_overrides" not in cpo_indexes:
        op.create_index("idx_customer_price_overrides", "customer_price_overrides",
                         ["customer_id", "product_id", "is_active"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "customers" in existing_tables:
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("customers")}
        if "fk_customer_price_list" in existing_fks:
            op.drop_constraint("fk_customer_price_list", "customers", type_="foreignkey")
    for table in ("cost_entries", "customer_price_overrides", "price_list_item_history",
                  "price_list_items", "price_lists"):
        if table in existing_tables:
            op.drop_table(table)
