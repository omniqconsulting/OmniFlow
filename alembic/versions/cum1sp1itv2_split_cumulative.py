"""fms_auto_split: cumulative-entered tracking on remainder splits

Revision ID: cum1sp1itv2
Revises: 4ut0sp1itn6
Create Date: 2026-07-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'cum1sp1itv2'
down_revision: Union[str, Sequence[str], None] = '4ut0sp1itn6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns('fms_ticket_splits')}
    if 'last_cumulative_entered' not in existing:
        op.add_column('fms_ticket_splits', sa.Column('last_cumulative_entered', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('fms_ticket_splits', 'last_cumulative_entered')
