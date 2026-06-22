"""add_custom_fields_json

Revision ID: 5d58e4fe76c1
Revises: 4915bed4df44
Create Date: 2026-06-22 14:35:03.030117

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d58e4fe76c1'
down_revision: Union[str, Sequence[str], None] = '4915bed4df44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('fms_stages', sa.Column('custom_fields_json', sa.Text(), nullable=True))
    op.add_column('library_flow_stages', sa.Column('custom_fields_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('library_flow_stages', 'custom_fields_json')
    op.drop_column('fms_stages', 'custom_fields_json')
