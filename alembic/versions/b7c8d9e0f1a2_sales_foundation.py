"""sales_foundation

Revision ID: b7c8d9e0f1a2
Revises: f2a3b4c5d6e7
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'units_of_measure' not in existing_tables:
        op.create_table(
            'units_of_measure',
            sa.Column('id',           sa.String(),   nullable=False),
            sa.Column('tenant_id',    sa.String(),   nullable=False),
            sa.Column('name',         sa.String(),   nullable=False),
            sa.Column('abbreviation', sa.String(),   nullable=False),
            sa.Column('is_active',    sa.Boolean(),  nullable=True),
            sa.Column('is_deleted',   sa.Boolean(),  nullable=True),
            sa.Column('created_at',   sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    existing_user_cols = [c['name'] for c in inspector.get_columns('users')]
    if 'module_access_json' not in existing_user_cols:
        op.add_column('users', sa.Column('module_access_json', sa.Text(), nullable=True, server_default='[]'))


def downgrade() -> None:
    op.drop_column('users', 'module_access_json')
    op.drop_table('units_of_measure')
