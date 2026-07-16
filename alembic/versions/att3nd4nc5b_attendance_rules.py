"""attendance_rules

Revision ID: att3nd4nc5b
Revises: att3nd4nc4b
Create Date: 2026-07-16 05:00:00.000000

Workstream B, Phase B5 — tenant-defined attendance classification rules.
Client confirmed (2026-07-16) there is no fixed catalog of ~10-15 rules;
instead every tenant needs to define their own custom rules (late arrival,
minimum hours, missed punch-out, etc.) from a fixed set of condition
fields/operators. Adds attendance_rules: one row per tenant-defined rule,
evaluated in ascending `priority` order, first match wins. Idempotent
(existing-tables check), same pattern as prior Attendance migrations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'att3nd4nc5b'
down_revision: Union[str, Sequence[str], None] = 'att3nd4nc4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "attendance_rules" not in existing_tables:
        op.create_table(
            "attendance_rules",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=True),
            sa.Column("conditions_json", sa.Text(), nullable=False),
            sa.Column("condition_logic", sa.String(), nullable=True),
            sa.Column("outcome", sa.String(), nullable=False),
            sa.Column("is_deleted", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    indexes = {ix["name"] for ix in inspector.get_indexes("attendance_rules")}
    if "idx_attendance_rules_tenant_priority" not in indexes:
        op.create_index("idx_attendance_rules_tenant_priority", "attendance_rules", ["tenant_id", "priority"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "attendance_rules" in inspector.get_table_names():
        op.drop_table("attendance_rules")
