"""add_ticket_form_fields_to_flows_and_tickets

Revision ID: f2a3b4c5d6e7
Revises: 89bcb52efbad
Create Date: 2026-06-29

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = '89bcb52efbad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text("ALTER TABLE fms_flows ADD COLUMN IF NOT EXISTS ticket_form_fields_json TEXT DEFAULT '[]'"))
        bind.execute(sa.text("ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS ticket_custom_fields_json TEXT"))
    else:
        try:
            op.add_column('fms_flows', sa.Column('ticket_form_fields_json', sa.Text(), nullable=True))
        except Exception:
            pass
        try:
            op.add_column('fms_tickets', sa.Column('ticket_custom_fields_json', sa.Text(), nullable=True))
        except Exception:
            pass


def downgrade() -> None:
    op.drop_column('fms_flows', 'ticket_form_fields_json')
    op.drop_column('fms_tickets', 'ticket_custom_fields_json')
