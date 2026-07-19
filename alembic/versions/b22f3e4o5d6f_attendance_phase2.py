"""attendance_phase2

Revision ID: b22f3e4o5d6f
Revises: a11e2d3n4c5e
Create Date: 2026-07-19

Attendance & Leave Phase 2 — branch-level weekly-off days (client feedback
#5: different branches of the same org can have weekly-off on different
days, excluded from Absent entirely) and the tenant-configurable attendance
rule engine (client feedback #6: generic condition-catalog rule builder to
decide PRESENT/HALF_DAY/ABSENT, first-match-wins). Idempotent, follows the
wa9ev1nttggl (columns) and a11e2d3n4c5e (new table) patterns from prior work.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b22f3e4o5d6f'
down_revision: Union[str, Sequence[str], None] = 'a11e2d3n4c5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    branches_cols = {c['name'] for c in inspector.get_columns('branches')}
    if 'weekly_off_days' not in branches_cols:
        with op.batch_alter_table('branches', schema=None) as batch_op:
            batch_op.add_column(sa.Column('weekly_off_days', sa.Text(), nullable=True, server_default='[6]'))
        op.execute("UPDATE branches SET weekly_off_days = '[6]' WHERE weekly_off_days IS NULL")

    existing_tables = inspector.get_table_names()
    if 'attendance_rules' not in existing_tables:
        op.create_table(
            "attendance_rules",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("priority", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("conditions_json", sa.Text(), nullable=False),
            sa.Column("condition_logic", sa.String(), nullable=True, server_default="ALL"),
            sa.Column("outcome", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if 'attendance_rules' in existing_tables:
        op.drop_table('attendance_rules')
    branches_cols = {c['name'] for c in inspector.get_columns('branches')}
    if 'weekly_off_days' in branches_cols:
        with op.batch_alter_table('branches', schema=None) as batch_op:
            batch_op.drop_column('weekly_off_days')
