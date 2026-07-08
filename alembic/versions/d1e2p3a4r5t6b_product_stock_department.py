"""product_stock_department

Revision ID: d1e2p3a4r5t6b
Revises: s1a2l3e4s5t6
Create Date: 2026-07-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd1e2p3a4r5t6b'
down_revision: Union[str, Sequence[str], None] = 's1a2l3e4s5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("product_stock")}
    if "uq_product_stock_variant_department" in {uc["name"] for uc in inspector.get_unique_constraints("product_stock")}:
        return  # already applied

    if bind.dialect.name == "sqlite":
        # SQLite can't ALTER a column-level UNIQUE constraint, and the existing
        # `UNIQUE (variant_id)` on this table is anonymous (SQLite doesn't
        # preserve inline-constraint names), which trips up Alembic's
        # batch/recreate machinery (it requires named constraints to copy).
        # Rebuild the table manually instead — same effect, full control.
        op.execute("""
            CREATE TABLE product_stock_new (
                id VARCHAR NOT NULL,
                variant_id VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                department_id VARCHAR,
                qty_available FLOAT,
                qty_reserved FLOAT,
                qty_in_transit FLOAT,
                avg_cost FLOAT,
                last_updated_at DATETIME,
                PRIMARY KEY (id),
                UNIQUE (variant_id, department_id),
                FOREIGN KEY(variant_id) REFERENCES product_variants (id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id),
                FOREIGN KEY(department_id) REFERENCES departments (id)
            )
        """)
        insert_cols = "id, variant_id, tenant_id, department_id, qty_available, qty_reserved, qty_in_transit, avg_cost, last_updated_at"
        select_cols = insert_cols if "department_id" in columns else \
            "id, variant_id, tenant_id, NULL, qty_available, qty_reserved, qty_in_transit, avg_cost, last_updated_at"
        op.execute(f"INSERT INTO product_stock_new ({insert_cols}) SELECT {select_cols} FROM product_stock")
        op.execute("DROP TABLE product_stock")
        op.execute("ALTER TABLE product_stock_new RENAME TO product_stock")
    else:
        if "department_id" not in columns:
            op.add_column(
                "product_stock",
                sa.Column("department_id", sa.String(), sa.ForeignKey("departments.id"), nullable=True),
            )
        for uc in inspector.get_unique_constraints("product_stock"):
            if uc["column_names"] == ["variant_id"]:
                op.drop_constraint(uc["name"], "product_stock", type_="unique")
        op.create_unique_constraint(
            "uq_product_stock_variant_department", "product_stock", ["variant_id", "department_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("product_stock")}

    if bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TABLE product_stock_new (
                id VARCHAR NOT NULL,
                variant_id VARCHAR NOT NULL,
                tenant_id VARCHAR NOT NULL,
                qty_available FLOAT,
                qty_reserved FLOAT,
                qty_in_transit FLOAT,
                avg_cost FLOAT,
                last_updated_at DATETIME,
                PRIMARY KEY (id),
                UNIQUE (variant_id),
                FOREIGN KEY(variant_id) REFERENCES product_variants (id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id)
            )
        """)
        op.execute("""
            INSERT INTO product_stock_new (id, variant_id, tenant_id, qty_available, qty_reserved, qty_in_transit, avg_cost, last_updated_at)
            SELECT id, variant_id, tenant_id, qty_available, qty_reserved, qty_in_transit, avg_cost, last_updated_at
            FROM product_stock WHERE department_id IS NULL
        """)
        op.execute("DROP TABLE product_stock")
        op.execute("ALTER TABLE product_stock_new RENAME TO product_stock")
    else:
        existing_uniques = {uc["name"] for uc in inspector.get_unique_constraints("product_stock")}
        if "uq_product_stock_variant_department" in existing_uniques:
            op.drop_constraint("uq_product_stock_variant_department", "product_stock", type_="unique")
        if "department_id" in columns:
            op.execute("DELETE FROM product_stock WHERE department_id IS NOT NULL")
            op.drop_column("product_stock", "department_id")
        op.create_unique_constraint("uq_product_stock_variant", "product_stock", ["variant_id"])
