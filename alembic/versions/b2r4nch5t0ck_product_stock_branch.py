"""product_stock: replace department_id with branch_id

Revision ID: b2r4nch5t0ck
Revises: p1r2c3h4s5r6
Create Date: 2026-07-09
"""
import uuid
from datetime import datetime
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b2r4nch5t0ck'
down_revision: Union[str, Sequence[str], None] = 'p1r2c3h4s5r6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("product_stock")}

    if "branch_id" not in cols:
        with op.batch_alter_table("product_stock") as batch_op:
            batch_op.add_column(sa.Column("branch_id", sa.String(), sa.ForeignKey("branches.id"), nullable=True))

    # ── Backfill: collapse department-scoped rows onto their branch ──
    if "department_id" in cols:
        dept_rows = bind.execute(sa.text(
            "SELECT id, branch_id FROM departments"
        )).fetchall()
        dept_branch = {r[0]: r[1] for r in dept_rows}

        stock_rows = bind.execute(sa.text(
            "SELECT id, variant_id, tenant_id, department_id, qty_available, qty_reserved, "
            "qty_in_transit, avg_cost, last_updated_at FROM product_stock WHERE department_id IS NOT NULL"
        )).fetchall()

        # (variant_id, branch_id) -> accumulated row
        merged: dict = {}
        for row in stock_rows:
            _id, variant_id, tenant_id, department_id, qty_available, qty_reserved, qty_in_transit, avg_cost, last_updated_at = row
            branch_id = dept_branch.get(department_id)
            if not branch_id:
                continue  # unassigned department — quantity already reflected in the aggregate row
            key = (variant_id, branch_id)
            if key not in merged:
                merged[key] = {
                    "tenant_id": tenant_id, "qty_available": 0.0, "qty_reserved": 0.0,
                    "qty_in_transit": 0.0, "avg_cost": avg_cost, "last_updated_at": last_updated_at,
                }
            m = merged[key]
            m["qty_available"] += qty_available or 0.0
            m["qty_reserved"] += qty_reserved or 0.0
            m["qty_in_transit"] += qty_in_transit or 0.0
            if last_updated_at and (not m["last_updated_at"] or last_updated_at > m["last_updated_at"]):
                m["last_updated_at"] = last_updated_at
                m["avg_cost"] = avg_cost

        # Existing branch-scoped rows may already exist (re-run safety) — merge into those instead of inserting dupes.
        existing_branch_rows = bind.execute(sa.text(
            "SELECT variant_id, branch_id, id FROM product_stock WHERE branch_id IS NOT NULL"
        )).fetchall()
        existing_by_key = {(r[0], r[1]): r[2] for r in existing_branch_rows}

        for (variant_id, branch_id), m in merged.items():
            existing_id = existing_by_key.get((variant_id, branch_id))
            if existing_id:
                bind.execute(sa.text(
                    "UPDATE product_stock SET qty_available = qty_available + :qa, "
                    "qty_reserved = qty_reserved + :qr, qty_in_transit = qty_in_transit + :qt "
                    "WHERE id = :id"
                ), {"qa": m["qty_available"], "qr": m["qty_reserved"], "qt": m["qty_in_transit"], "id": existing_id})
            else:
                bind.execute(sa.text(
                    "INSERT INTO product_stock (id, variant_id, tenant_id, branch_id, qty_available, "
                    "qty_reserved, qty_in_transit, avg_cost, last_updated_at) VALUES "
                    "(:id, :variant_id, :tenant_id, :branch_id, :qa, :qr, :qt, :avg_cost, :lu)"
                ), {
                    "id": str(uuid.uuid4()), "variant_id": variant_id, "tenant_id": m["tenant_id"],
                    "branch_id": branch_id, "qa": m["qty_available"], "qr": m["qty_reserved"],
                    "qt": m["qty_in_transit"], "avg_cost": m["avg_cost"],
                    "lu": m["last_updated_at"] or datetime.utcnow(),
                })

        bind.execute(sa.text("DELETE FROM product_stock WHERE department_id IS NOT NULL"))

        with op.batch_alter_table("product_stock") as batch_op:
            batch_op.drop_column("department_id")

    # ── Unique constraint: (variant_id, department_id) -> (variant_id, branch_id) ──
    inspector = sa.inspect(bind)
    existing_constraints = {c["name"] for c in inspector.get_unique_constraints("product_stock")}
    if "uq_product_stock_variant_branch" not in existing_constraints:
        with op.batch_alter_table("product_stock") as batch_op:
            if "uq_product_stock_variant_department" in existing_constraints:
                batch_op.drop_constraint("uq_product_stock_variant_department", type_="unique")
            batch_op.create_unique_constraint("uq_product_stock_variant_branch", ["variant_id", "branch_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("product_stock")}
    with op.batch_alter_table("product_stock") as batch_op:
        if "uq_product_stock_variant_branch" in {c["name"] for c in inspector.get_unique_constraints("product_stock")}:
            batch_op.drop_constraint("uq_product_stock_variant_branch", type_="unique")
        if "department_id" not in cols:
            batch_op.add_column(sa.Column("department_id", sa.String(), sa.ForeignKey("departments.id"), nullable=True))
        if "branch_id" in cols:
            batch_op.drop_column("branch_id")
