"""add_target_tat_unit_and_widen_tat_hours_to_float

Revision ID: t1a2t3u4n5i6
Revises: a1i2i3n4t5e6
Create Date: 2026-07-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 't1a2t3u4n5i6'
down_revision: Union[str, Sequence[str], None] = 'a1i2i3n4t5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text(
            "ALTER TABLE fms_stages ALTER COLUMN target_tat_hours TYPE DOUBLE PRECISION "
            "USING target_tat_hours::double precision"
        ))
        bind.execute(sa.text(
            "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS target_tat_unit VARCHAR DEFAULT 'hours'"
        ))
    else:
        # SQLite has no strict column typing (type affinity only), so fractional
        # hours already store fine without an ALTER — just add the new column.
        try:
            op.add_column('fms_stages', sa.Column('target_tat_unit', sa.String(), nullable=True, server_default='hours'))
        except Exception:
            pass


def downgrade() -> None:
    op.drop_column('fms_stages', 'target_tat_unit')
