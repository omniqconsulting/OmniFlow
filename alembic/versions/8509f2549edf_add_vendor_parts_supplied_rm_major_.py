"""cleanup: drop removed inventory tables and obsolete label columns

Revision ID: 8509f2549edf
Revises: a1b2c3d4e5f6
Create Date: 2026-06-17 15:51:08.896196

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8509f2549edf'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop inventory tables and removed label columns using IF EXISTS to avoid transaction abort."""
    conn = op.get_bind()

    # Drop inventory tables children-first using IF EXISTS (safe on both SQLite and PostgreSQL)
    for tbl in (
        'stock_movements',
        'material_requests',
        'purchase_order_items',
        'purchase_orders',
        'materials',
    ):
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {tbl}"))

    # Drop obsolete inventory-related label columns using IF EXISTS
    obsolete_cols = [
        'material_s', 'material_p',
        'store_manager_s', 'store_manager_p',
        'inventory_s', 'inventory_p',
        'purchase_order_s', 'purchase_order_p',
        'supplier_s', 'supplier_p',
        'stock_in_s', 'stock_out_s',
        'adjustment_s',
    ]
    dialect = conn.dialect.name
    for col in obsolete_cols:
        if dialect == 'sqlite':
            # SQLite doesn't support IF EXISTS on DROP COLUMN — use batch_alter_table
            try:
                with op.batch_alter_table('tenant_label_configs') as batch_op:
                    batch_op.drop_column(col)
            except Exception:
                pass
        else:
            # PostgreSQL supports IF EXISTS on ALTER TABLE DROP COLUMN
            conn.execute(sa.text(
                f"ALTER TABLE tenant_label_configs DROP COLUMN IF EXISTS {col}"
            ))


def downgrade() -> None:
    """Downgrade is not supported for this destructive migration."""
    pass
