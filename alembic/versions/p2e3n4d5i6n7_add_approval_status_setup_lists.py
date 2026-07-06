"""add approval_status to setup list tables

Ticket creation's Linked Entities picker can now register a brand-new
customer/vendor/material/end-product/custom-list name directly from the
create-ticket form. That row is created immediately (so it can be linked
right away) but flagged approval_status='PENDING' until an Admin/Manager
reviews it in Setup and fills in the remaining columns (any edit save
flips it back to 'APPROVED'). Existing rows default to 'APPROVED' so
nothing already in the system is affected.

Revision ID: p2e3n4d5i6n7
Revises: f1e2l3d4e5d6
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'p2e3n4d5i6n7'
down_revision: Union[str, Sequence[str], None] = 'f1e2l3d4e5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ["vendors", "raw_materials", "customers", "end_products", "custom_reference_items"]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in _TABLES:
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "approval_status" not in cols:
            op.add_column(table, sa.Column("approval_status", sa.String(), nullable=True, server_default="APPROVED"))
    for t in _TABLES:
        conn.execute(sa.text(f"UPDATE {t} SET approval_status = 'APPROVED' WHERE approval_status IS NULL"))


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    for table in _TABLES:
        inspector = sa.inspect(conn)
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "approval_status" not in cols:
            continue
        if dialect == "sqlite":
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column("approval_status")
        else:
            op.drop_column(table, "approval_status")
