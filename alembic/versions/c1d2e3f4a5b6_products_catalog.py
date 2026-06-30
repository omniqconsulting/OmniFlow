"""products_catalog

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'product_schema_fields' not in existing_tables:
        op.create_table(
            'product_schema_fields',
            sa.Column('id',           sa.String(),   nullable=False),
            sa.Column('tenant_id',    sa.String(),   nullable=False),
            sa.Column('label',        sa.String(),   nullable=False),
            sa.Column('field_type',   sa.String(),   nullable=True),
            sa.Column('options_json', sa.Text(),     nullable=True),
            sa.Column('sort_order',   sa.Integer(),  nullable=True),
            sa.Column('is_required',  sa.Boolean(),  nullable=True),
            sa.Column('is_active',    sa.Boolean(),  nullable=True),
            sa.Column('created_at',   sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'products' not in existing_tables:
        op.create_table(
            'products',
            sa.Column('id',                  sa.String(),  nullable=False),
            sa.Column('tenant_id',           sa.String(),  nullable=False),
            sa.Column('sku_code',            sa.String(),  nullable=False),
            sa.Column('name',                sa.String(),  nullable=False),
            sa.Column('description',         sa.Text(),    nullable=True),
            sa.Column('category',            sa.String(),  nullable=True),
            sa.Column('base_unit_id',        sa.String(),  nullable=True),
            sa.Column('attributes_json',     sa.Text(),    nullable=True),
            sa.Column('media_urls_json',     sa.Text(),    nullable=True),
            sa.Column('product_tier',        sa.String(),  nullable=True),
            sa.Column('low_stock_threshold', sa.Float(),   nullable=True),
            sa.Column('is_active',           sa.Boolean(), nullable=True),
            sa.Column('is_deleted',          sa.Boolean(), nullable=True),
            sa.Column('created_by_id',       sa.String(),  nullable=True),
            sa.Column('created_at',          sa.DateTime(), nullable=True),
            sa.Column('updated_at',          sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('products')
    op.drop_table('product_schema_fields')
