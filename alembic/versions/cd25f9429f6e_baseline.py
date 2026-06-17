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
    """Drop removed inventory tables (children first) and obsolete label columns.

    Note: all alter_column calls from the original autogenerate were SQLite-specific
    type differences (INTEGER vs BOOLEAN, TEXT vs VARCHAR) — they are no-ops on
    PostgreSQL where SQLAlchemy already creates the correct types, so they are omitted
    here to avoid errors on production.
    """
    conn = op.get_bind()

    # Drop inventory tables — children before parents to avoid FK constraint errors
    for tbl in (
        'stock_movements',       # refs materials, purchase_order_items
        'material_requests',     # refs materials
        'purchase_order_items',  # refs purchase_orders, materials
        'purchase_orders',       # parent
        'materials',             # parent
    ):
        try:
            op.drop_table(tbl)
        except Exception:
            pass  # already dropped or never existed

    # Drop obsolete inventory-related label columns from tenant_label_configs
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
        try:
            with op.batch_alter_table('tenant_label_configs') as batch_op:
                batch_op.drop_column(col)
        except Exception:
            pass  # already dropped or column doesn't exist


def downgrade() -> None:
    """Downgrade is not supported for this destructive migration."""
    pass
