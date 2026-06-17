"""baseline

Revision ID: cd25f9429f6e
Revises:
Create Date: 2026-06-16 21:14:54.036780

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cd25f9429f6e'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop removed inventory tables and obsolete label columns.

    Uses IF EXISTS so no exception is raised on missing tables/columns — this prevents
    PostgreSQL from aborting the transaction (which would block the alembic_version update).

    All alter_column type-change ops from the original autogenerate are omitted — they
    were SQLite-specific false positives (SQLite stores Boolean as INTEGER; PostgreSQL
    already uses the correct types).
    """
    conn = op.get_bind()
    dialect = conn.dialect.name

    # Drop inventory tables children-first using IF EXISTS
    for tbl in (
        'stock_movements',       # refs materials, purchase_order_items
        'material_requests',     # refs materials
        'purchase_order_items',  # refs purchase_orders, materials
        'purchase_orders',       # parent
        'materials',             # parent
    ):
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {tbl}"))

    # Drop obsolete inventory-related label columns
    obsolete_cols = [
        'material_s', 'material_p',
        'store_manager_s', 'store_manager_p',
        'inventory_s', 'inventory_p',
        'purchase_order_s', 'purchase_order_p',
        'supplier_s', 'supplier_p',
        'stock_in_s', 'stock_out_s',
        'adjustment_s',
    ]
    for col in obsolete_cols:
        if dialect == 'sqlite':
            try:
                with op.batch_alter_table('tenant_label_configs') as batch_op:
                    batch_op.drop_column(col)
            except Exception:
                pass
        else:
            conn.execute(sa.text(
                f"ALTER TABLE tenant_label_configs DROP COLUMN IF EXISTS {col}"
            ))


def downgrade() -> None:
    """Downgrade is not supported for this destructive migration."""
    pass
