"""add_checklist_frequency_type_config

Revision ID: c97831d82bc5
Revises: 05875ebc7c8b
Create Date: 2026-06-26 22:27:16.934197

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c97831d82bc5'
down_revision: Union[str, Sequence[str], None] = '05875ebc7c8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add frequency_type and frequency_config columns to checklist_templates (E-14)."""
    with op.batch_alter_table('checklist_templates') as batch_op:
        batch_op.add_column(sa.Column('frequency_type', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('frequency_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove E-14 frequency columns."""
    with op.batch_alter_table('checklist_templates') as batch_op:
        batch_op.drop_column('frequency_config')
        batch_op.drop_column('frequency_type')
