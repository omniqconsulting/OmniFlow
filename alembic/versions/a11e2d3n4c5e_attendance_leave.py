"""attendance_leave

Revision ID: a11e2d3n4c5e
Revises: g2u3p4s5h6u7
Create Date: 2026-07-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a11e2d3n4c5e'
down_revision: Union[str, Sequence[str], None] = 'g2u3p4s5h6u7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "attendance_geofences" not in existing_tables:
        op.create_table(
            "attendance_geofences",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("branch_id", sa.String(), sa.ForeignKey("branches.id"), nullable=True),
            sa.Column("site_name", sa.String(), nullable=False, server_default="Main Office"),
            sa.Column("center_lat", sa.Float(), nullable=False),
            sa.Column("center_lng", sa.Float(), nullable=False),
            sa.Column("radius_m", sa.Integer(), nullable=False, server_default="200"),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "attendance_records" not in existing_tables:
        op.create_table(
            "attendance_records",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("work_date", sa.Date(), nullable=False),
            sa.Column("check_in_at", sa.DateTime(), nullable=True),
            sa.Column("check_in_lat", sa.Float(), nullable=True),
            sa.Column("check_in_lng", sa.Float(), nullable=True),
            sa.Column("check_in_in_fence", sa.Boolean(), nullable=True),
            sa.Column("check_in_reason", sa.Text(), nullable=True),
            sa.Column("check_out_at", sa.DateTime(), nullable=True),
            sa.Column("check_out_lat", sa.Float(), nullable=True),
            sa.Column("check_out_lng", sa.Float(), nullable=True),
            sa.Column("check_out_in_fence", sa.Boolean(), nullable=True),
            sa.Column("check_out_reason", sa.Text(), nullable=True),
            sa.Column("photo_path", sa.String(), nullable=True),
            sa.Column("is_half_day", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "work_date", name="uq_attendance_user_day"),
        )

    if "leave_requests" not in existing_tables:
        op.create_table(
            "leave_requests",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("leave_type", sa.String(), nullable=False, server_default="CASUAL"),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("is_half_day", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=True, server_default="PENDING"),
            sa.Column("approver_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "leave_requests" in existing_tables:
        op.drop_table("leave_requests")
    if "attendance_records" in existing_tables:
        op.drop_table("attendance_records")
    if "attendance_geofences" in existing_tables:
        op.drop_table("attendance_geofences")
