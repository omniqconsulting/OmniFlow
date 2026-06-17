"""add evidence_required to library_flow_stages

Revision ID: b1c2d3e4f5a6
Revises: 8509f2549edf
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = '8509f2549edf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == 'sqlite':
        try:
            op.add_column('library_flow_stages', sa.Column('evidence_required', sa.Boolean(), nullable=True, server_default='0'))
        except Exception:
            pass
    else:
        conn.execute(sa.text(
            "ALTER TABLE library_flow_stages ADD COLUMN IF NOT EXISTS evidence_required BOOLEAN DEFAULT FALSE"
        ))


def downgrade() -> None:
    try:
        op.drop_column('library_flow_stages', 'evidence_required')
    except Exception:
        pass
