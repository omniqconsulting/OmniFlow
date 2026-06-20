"""add_whatsapp_foundation

Revision ID: 79c0a2fc820a
Revises: 9d9e0f2d796a
Create Date: 2026-06-20 23:32:47.360289

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '79c0a2fc820a'
down_revision: Union[str, Sequence[str], None] = '9d9e0f2d796a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — idempotent for SQLite compatibility."""
    from sqlalchemy import inspect, text
    bind = op.get_bind()
    inspector = inspect(bind)

    # Create whatsapp_message_log if not already present
    if 'whatsapp_message_log' not in inspector.get_table_names():
        op.create_table('whatsapp_message_log',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('template_name', sa.String(), nullable=False),
        sa.Column('recipient_user_id', sa.String(), nullable=True),
        sa.Column('recipient_phone', sa.String(), nullable=False),
        sa.Column('variables_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('related_entity_type', sa.String(), nullable=True),
        sa.Column('related_entity_id', sa.String(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_attempted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['recipient_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
        )

    # Add mobile_verified columns if not already present
    user_cols = {c['name'] for c in inspector.get_columns('users')}
    if 'mobile_verified' not in user_cols:
        op.add_column('users', sa.Column('mobile_verified', sa.Boolean(), nullable=True, server_default='0'))
    if 'mobile_verified_at' not in user_cols:
        op.add_column('users', sa.Column('mobile_verified_at', sa.DateTime(), nullable=True))
    if 'mobile_verified_by' not in user_cols:
        op.add_column('users', sa.Column('mobile_verified_by', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'mobile_verified_by')
    op.drop_column('users', 'mobile_verified_at')
    op.drop_column('users', 'mobile_verified')
    op.drop_table('whatsapp_message_log')
