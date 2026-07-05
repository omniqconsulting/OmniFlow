"""add fms_field_edit_log table

Table-view manual cell edits on FMS ticket/stage custom columns need an
audit trail (who changed what, when, and why) separate from the normal
stage-transition FMSEvent log — see app/fms.py fms_table_cell_edit.

Revision ID: f1e2l3d4e5d6
Revises: f1r2a3n4c5h6
Create Date: 2026-07-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f1e2l3d4e5d6'
down_revision: Union[str, Sequence[str], None] = 'f1r2a3n4c5h6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "fms_field_edit_log" in inspector.get_table_names():
        return
    op.create_table(
        "fms_field_edit_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("ticket_id", sa.String(), sa.ForeignKey("fms_tickets.id"), nullable=False),
        sa.Column("stage_id", sa.String(), sa.ForeignKey("fms_stages.id"), nullable=True),
        sa.Column("field_id", sa.String(), nullable=False),
        sa.Column("field_label", sa.String(), nullable=True),
        sa.Column("old_value", sa.String(), nullable=True),
        sa.Column("new_value", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("is_cascade", sa.Boolean(), server_default=sa.text("false" if conn.dialect.name != "sqlite" else "0")),
        sa.Column("edited_by_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("fms_field_edit_log")
