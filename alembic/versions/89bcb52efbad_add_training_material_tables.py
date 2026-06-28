"""add_training_material_tables

Revision ID: 89bcb52efbad
Revises: k1n2o3w4l5e6
Create Date: 2026-06-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '89bcb52efbad'
down_revision: Union[str, Sequence[str], None] = 'k1n2o3w4l5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    if 'training_material_categories' not in existing:
        op.create_table(
            'training_material_categories',
            sa.Column('id',         sa.String(),   nullable=False),
            sa.Column('tenant_id',  sa.String(),   nullable=False),
            sa.Column('name',       sa.String(),   nullable=False),
            sa.Column('is_active',  sa.Boolean(),  nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'training_materials' not in existing:
        op.create_table(
            'training_materials',
            sa.Column('id',             sa.String(),   nullable=False),
            sa.Column('tenant_id',      sa.String(),   nullable=False),
            sa.Column('title',          sa.String(),   nullable=False),
            sa.Column('description',    sa.Text(),     nullable=True),
            sa.Column('file_name',      sa.String(),   nullable=False),
            sa.Column('file_path',      sa.String(),   nullable=False),
            sa.Column('file_type',      sa.String(),   nullable=True),
            sa.Column('file_size',      sa.Integer(),  nullable=True),
            sa.Column('category',       sa.String(),   nullable=True),
            sa.Column('department_id',  sa.String(),   nullable=True),
            sa.Column('tags',           sa.String(),   nullable=True),
            sa.Column('uploaded_by_id', sa.String(),   nullable=False),
            sa.Column('is_deleted',     sa.Boolean(),  nullable=True),
            sa.Column('created_at',     sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('training_materials')
    op.drop_table('training_material_categories')
