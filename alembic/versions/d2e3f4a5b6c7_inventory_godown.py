"""inventory_godown

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'product_stock' not in existing_tables:
        op.create_table(
            'product_stock',
            sa.Column('id',              sa.String(),   nullable=False),
            sa.Column('product_id',      sa.String(),   nullable=False),
            sa.Column('tenant_id',       sa.String(),   nullable=False),
            sa.Column('qty_available',   sa.Float(),    nullable=True),
            sa.Column('qty_reserved',    sa.Float(),    nullable=True),
            sa.Column('qty_in_transit',  sa.Float(),    nullable=True),
            sa.Column('avg_cost',        sa.Float(),    nullable=True),
            sa.Column('last_updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('product_id'),
        )

    if 'stock_ledger' not in existing_tables:
        op.create_table(
            'stock_ledger',
            sa.Column('id',             sa.String(),   nullable=False),
            sa.Column('tenant_id',      sa.String(),   nullable=False),
            sa.Column('product_id',     sa.String(),   nullable=False),
            sa.Column('movement_type',  sa.String(),   nullable=False),
            sa.Column('qty',            sa.Float(),    nullable=False),
            sa.Column('unit_cost',      sa.Float(),    nullable=True),
            sa.Column('reference_type', sa.String(),   nullable=True),
            sa.Column('reference_id',   sa.String(),   nullable=True),
            sa.Column('notes',          sa.Text(),     nullable=True),
            sa.Column('actor_id',       sa.String(),   nullable=True),
            sa.Column('created_at',     sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'inventory_purchase_orders' not in existing_tables:
        op.create_table(
            'inventory_purchase_orders',
            sa.Column('id',                    sa.String(),   nullable=False),
            sa.Column('tenant_id',             sa.String(),   nullable=False),
            sa.Column('display_id',            sa.String(),   nullable=True),
            sa.Column('vendor_id',             sa.String(),   nullable=True),
            sa.Column('vendor_name_snapshot',  sa.String(),   nullable=True),
            sa.Column('status',                sa.String(),   nullable=True),
            sa.Column('expected_arrival_date', sa.Date(),     nullable=True),
            sa.Column('notes',                 sa.Text(),     nullable=True),
            sa.Column('created_by_id',         sa.String(),   nullable=True),
            sa.Column('approved_by_id',        sa.String(),   nullable=True),
            sa.Column('is_deleted',            sa.Boolean(),  nullable=True),
            sa.Column('created_at',            sa.DateTime(), nullable=True),
            sa.Column('updated_at',            sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'inventory_po_items' not in existing_tables:
        op.create_table(
            'inventory_po_items',
            sa.Column('id',           sa.String(), nullable=False),
            sa.Column('po_id',        sa.String(), nullable=False),
            sa.Column('product_id',   sa.String(), nullable=False),
            sa.Column('qty_ordered',  sa.Float(),  nullable=False),
            sa.Column('qty_received', sa.Float(),  nullable=True),
            sa.Column('unit_cost',    sa.Float(),  nullable=True),
            sa.Column('unit_id',      sa.String(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    inspector = sa.inspect(bind)
    existing_indexes_ledger = {ix['name'] for ix in inspector.get_indexes('stock_ledger')}
    if 'idx_stock_ledger_product' not in existing_indexes_ledger:
        op.create_index('idx_stock_ledger_product', 'stock_ledger', ['product_id', 'created_at'])

    existing_indexes_po = {ix['name'] for ix in inspector.get_indexes('inventory_purchase_orders')}
    if 'idx_po_status' not in existing_indexes_po:
        op.create_index('idx_po_status', 'inventory_purchase_orders', ['tenant_id', 'status'])


def downgrade() -> None:
    op.drop_index('idx_po_status', table_name='inventory_purchase_orders')
    op.drop_index('idx_stock_ledger_product', table_name='stock_ledger')
    op.drop_table('inventory_po_items')
    op.drop_table('inventory_purchase_orders')
    op.drop_table('stock_ledger')
    op.drop_table('product_stock')
