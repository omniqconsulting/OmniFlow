"""add vendor parts_supplied and raw_material major_supplier

Revision ID: a1b2c3d4e5f6
Revises: cd25f9429f6e
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'cd25f9429f6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    try:
        op.add_column('vendors', sa.Column('parts_supplied', sa.Text(), nullable=True))
    except Exception:
        pass  # column already exists
    try:
        op.add_column('raw_materials', sa.Column('major_supplier', sa.String(), nullable=True))
    except Exception:
        pass  # column already exists


def downgrade() -> None:
    try:
        op.drop_column('vendors', 'parts_supplied')
    except Exception:
        pass
    try:
        op.drop_column('raw_materials', 'major_supplier')
    except Exception:
        pass
