"""purchase_requests

Revision ID: p1r2c3h4s5r6
Revises: d1sp4tch5qu6u
Create Date: 2026-07-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'p1r2c3h4s5r6'
down_revision: Union[str, Sequence[str], None] = 'd1sp4tch5qu6u'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "purchase_requests" not in existing_tables:
        op.create_table(
            "purchase_requests",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("variant_id", sa.String(), sa.ForeignKey("product_variants.id"), nullable=False),
            sa.Column("requested_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("qty_requested", sa.Float(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("po_id", sa.String(), sa.ForeignKey("inventory_purchase_orders.id"), nullable=True),
            sa.Column("resolved_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    indexes = {ix["name"] for ix in inspector.get_indexes("purchase_requests")}
    if "idx_purchase_requests_tenant_status" not in indexes:
        op.create_index("idx_purchase_requests_tenant_status", "purchase_requests", ["tenant_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "purchase_requests" in inspector.get_table_names():
        op.drop_table("purchase_requests")
