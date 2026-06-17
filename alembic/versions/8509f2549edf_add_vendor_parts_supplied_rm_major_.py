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
    """Drop inventory tables and removed label columns (Inventory module removed in V2.2)."""
    conn = op.get_bind()
    dialect = conn.dialect.name

    # Drop inventory tables (removed from scope in V2.2)
    for tbl in ('purchase_order_items', 'stock_movements', 'material_requests',
                'purchase_orders', 'materials'):
        try:
            op.drop_table(tbl)
        except Exception:
            pass  # already dropped or never existed on this DB

    # Drop obsolete inventory-related label columns from tenant_label_configs
    obsolete_cols = [
        'material_s', 'material_p', 'store_manager_s', 'store_manager_p',
        'inventory_s', 'inventory_p', 'purchase_order_s', 'purchase_order_p',
        'supplier_s', 'supplier_p', 'stock_in_s', 'stock_out_s',
        'adjustment_s',
    ]
    for col in obsolete_cols:
        try:
            with op.batch_alter_table('tenant_label_configs') as batch_op:
                batch_op.drop_column(col)
        except Exception:
            pass  # already dropped or column doesn't exist


def downgrade() -> None:
    """Downgrade is not supported for this destructive migration."""
    pass
