"""replace godown with branch on sales_orders

Godown (app/database.py Godown/godowns) duplicated Setup's Branch entity —
both represent "a physical location". Per user request, Sales Order's
dispatch-location dropdown now sources from Branch directly. This migration
adds sales_orders.branch_id, backfills it from the existing godown_id data
(matching godowns.name to branches.name per tenant, creating a Branch when
no match exists so no order loses its dispatch-location data), then drops
godown_id and the godowns table.

Revision ID: f1r2a3n4c5h6
Revises: e1p2r3o4d5c6
Create Date: 2026-07-04
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = 'f1r2a3n4c5h6'
down_revision: Union[str, Sequence[str], None] = 'e1p2r3o4d5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    so_cols = {c["name"] for c in inspector.get_columns("sales_orders")} if "sales_orders" in existing_tables else set()
    if "branch_id" not in so_cols:
        op.add_column("sales_orders", sa.Column("branch_id", sa.String(), nullable=True))

    if "godowns" in existing_tables and "godown_id" in so_cols:
        orders = conn.execute(sa.text(
            "SELECT id, tenant_id, godown_id FROM sales_orders WHERE godown_id IS NOT NULL"
        )).mappings().all()
        godown_name_by_id = {
            row["id"]: (row["tenant_id"], row["name"])
            for row in conn.execute(sa.text("SELECT id, tenant_id, name FROM godowns")).mappings().all()
        }
        branch_id_cache = {}  # (tenant_id, lower(name)) -> branch_id
        for order in orders:
            info = godown_name_by_id.get(order["godown_id"])
            if not info:
                continue
            tenant_id, name = info
            key = (tenant_id, (name or "").strip().lower())
            if key not in branch_id_cache:
                match = conn.execute(sa.text(
                    "SELECT id FROM branches WHERE tenant_id = :tenant_id AND lower(name) = :name"
                ), {"tenant_id": tenant_id, "name": key[1]}).first()
                if match:
                    branch_id_cache[key] = match[0]
                else:
                    new_branch_id = str(uuid.uuid4())
                    conn.execute(sa.text(
                        "INSERT INTO branches (id, tenant_id, name, is_deleted) VALUES (:id, :tenant_id, :name, 0)"
                    ), {"id": new_branch_id, "tenant_id": tenant_id, "name": name})
                    branch_id_cache[key] = new_branch_id
            conn.execute(sa.text(
                "UPDATE sales_orders SET branch_id = :branch_id WHERE id = :id"
            ), {"branch_id": branch_id_cache[key], "id": order["id"]})

    inspector = sa.inspect(conn)
    so_cols = {c["name"] for c in inspector.get_columns("sales_orders")}
    if "godown_id" in so_cols:
        if dialect == "sqlite":
            with op.batch_alter_table("sales_orders") as batch_op:
                batch_op.drop_column("godown_id")
        else:
            for fk in inspector.get_foreign_keys("sales_orders"):
                if "godown_id" in (fk.get("constrained_columns") or []) and fk.get("name"):
                    try:
                        op.drop_constraint(fk["name"], "sales_orders", type_="foreignkey")
                    except Exception:
                        pass
            conn.execute(sa.text("ALTER TABLE sales_orders DROP COLUMN IF EXISTS godown_id"))

    existing_tables = sa.inspect(conn).get_table_names()
    if "godowns" in existing_tables:
        op.drop_table("godowns")


def downgrade() -> None:
    """Best-effort — recreates godowns/godown_id shape but does not restore
    the original per-godown rows (branch/godown merge is lossy to reverse)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "godowns" not in existing_tables:
        op.create_table(
            "godowns",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    so_cols = {c["name"] for c in inspector.get_columns("sales_orders")}
    if "godown_id" not in so_cols:
        op.add_column("sales_orders", sa.Column("godown_id", sa.String(), nullable=True))
    if "branch_id" in so_cols:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            with op.batch_alter_table("sales_orders") as batch_op:
                batch_op.drop_column("branch_id")
        else:
            conn.execute(sa.text("ALTER TABLE sales_orders DROP COLUMN IF EXISTS branch_id"))
