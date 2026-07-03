"""add ticket_knowledge_links table

Phase 3 (Delegation/Ticket UX): join table linking a ticket (typically closed
delegations) to a KnowledgeItem, so a closed ticket can reference relevant
Knowledge/Training content for future lookup.

Revision ID: h7i8j9k0l1m2
Revises: d1e2p3a4r5t6
Create Date: 2026-07-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'h7i8j9k0l1m2'
down_revision: Union[str, Sequence[str], None] = 'd1e2p3a4r5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "ticket_knowledge_links" in inspector.get_table_names():
        return
    op.create_table(
        "ticket_knowledge_links",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("ticket_id", sa.String(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("knowledge_item_id", sa.String(), sa.ForeignKey("knowledge_items.id"), nullable=False),
        sa.Column("linked_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "ticket_knowledge_links" in inspector.get_table_names():
        op.drop_table("ticket_knowledge_links")
