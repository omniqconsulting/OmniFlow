"""add_checklist_overdue_hour

Revision ID: 4915bed4df44
Revises: 79c0a2fc820a
Create Date: 2026-06-22 12:16:46.150971

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4915bed4df44'
down_revision: Union[str, Sequence[str], None] = '79c0a2fc820a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('checklist_overdue_hour', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('tenants', 'checklist_overdue_hour')
