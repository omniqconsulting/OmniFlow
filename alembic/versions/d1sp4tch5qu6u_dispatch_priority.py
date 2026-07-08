"""dispatch_priority

Revision ID: d1sp4tch5qu6u
Revises: d1e2p3a4r5t6b
Create Date: 2026-07-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd1sp4tch5qu6u'
down_revision: Union[str, Sequence[str], None] = 'd1e2p3a4r5t6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("sales_orders")}

    if "dispatch_priority" not in columns:
        op.add_column("sales_orders", sa.Column("dispatch_priority", sa.Integer(), nullable=True))

    # Backfill existing CONFIRMED orders in created_at order so the queue
    # preserves today's FIFO behaviour on upgrade instead of showing an
    # unordered pile the first time anyone opens the Dispatch Queue.
    rows = bind.execute(sa.text(
        "SELECT id FROM sales_orders WHERE status = 'CONFIRMED' AND is_deleted = false "
        "ORDER BY created_at ASC"
    )).fetchall()
    for i, row in enumerate(rows):
        bind.execute(
            sa.text("UPDATE sales_orders SET dispatch_priority = :p WHERE id = :id"),
            {"p": i, "id": row[0]},
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("sales_orders")}
    if "dispatch_priority" in columns:
        # Plain column, no constraints — safe as raw SQL on both SQLite
        # (3.35+ supports DROP COLUMN directly) and Postgres.
        op.execute("ALTER TABLE sales_orders DROP COLUMN dispatch_priority")
