"""add category_id/sub_category_id to end_products

Setup > End Products gains the same Category -> SubCategory hierarchy used by
the Sales Catalog (Category/SubCategory/Product/ProductVariant), so both
sides of the sku_code-matched sync in app/sales_catalog_sync.py can carry and
create hierarchy data bidirectionally.

Revision ID: e1p2r3o4d5c6
Revises: h7i8j9k0l1m2
Create Date: 2026-07-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e1p2r3o4d5c6'
down_revision: Union[str, Sequence[str], None] = 'h7i8j9k0l1m2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c["name"] for c in inspector.get_columns("end_products")}
    if "category_id" not in cols:
        op.add_column("end_products", sa.Column("category_id", sa.String(), nullable=True))
    if "sub_category_id" not in cols:
        op.add_column("end_products", sa.Column("sub_category_id", sa.String(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    inspector = sa.inspect(conn)
    cols = {c["name"] for c in inspector.get_columns("end_products")}
    if dialect == "sqlite":
        with op.batch_alter_table("end_products") as batch_op:
            if "sub_category_id" in cols:
                batch_op.drop_column("sub_category_id")
            if "category_id" in cols:
                batch_op.drop_column("category_id")
    else:
        if "sub_category_id" in cols:
            op.drop_column("end_products", "sub_category_id")
        if "category_id" in cols:
            op.drop_column("end_products", "category_id")
