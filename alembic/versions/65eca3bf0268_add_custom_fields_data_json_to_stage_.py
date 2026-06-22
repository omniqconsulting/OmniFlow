"""add_custom_fields_data_json_to_stage_history

Revision ID: 65eca3bf0268
Revises: 5d58e4fe76c1
Create Date: 2026-06-22 15:41:58.628944

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '65eca3bf0268'
down_revision: Union[str, Sequence[str], None] = '5d58e4fe76c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text("ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS custom_fields_data_json TEXT"))
    else:
        try:
            op.add_column('fms_stage_history', sa.Column('custom_fields_data_json', sa.Text(), nullable=True))
        except Exception:
            pass


def downgrade() -> None:
    op.drop_column('fms_stage_history', 'custom_fields_data_json')
