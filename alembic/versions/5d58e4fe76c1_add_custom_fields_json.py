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
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text("ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS custom_fields_json TEXT DEFAULT '[]'"))
        bind.execute(sa.text("ALTER TABLE library_flow_stages ADD COLUMN IF NOT EXISTS custom_fields_json TEXT DEFAULT '[]'"))
    else:
        try:
            op.add_column('fms_stages', sa.Column('custom_fields_json', sa.Text(), nullable=True))
        except Exception:
            pass
        try:
            op.add_column('library_flow_stages', sa.Column('custom_fields_json', sa.Text(), nullable=True))
        except Exception:
            pass


def downgrade() -> None:
    op.drop_column('library_flow_stages', 'custom_fields_json')
    op.drop_column('fms_stages', 'custom_fields_json')
