"""attendance_b1_schema

Revision ID: att3nd4nc1b
Revises: c0llections4a
Create Date: 2026-07-16 03:00:00.000000

Workstream B (Attendance & Leave Module), Phase B1 — schema only, fully inert
until the tenant is opted into ATTENDANCE_MODULE via FEATURE_CATALOG /
TenantFeatureOverride (see app/constants.py). Adds three new tables:
  - attendance_geofences — per-tenant/site (branch) geofence config used by
    B2's punch-in validation. branch_id=None is a tenant-wide default.
  - attendance_records — one row per employee per calendar day, single
    check-in/check-out pair (B2 scope, not multiple punches/day).
  - leave_requests — employee leave application/approval workflow (B3 scope
    builds the UI on top of this).
Extends the app via new related tables only — the existing users/employees
table is not altered in place, per the standing scope rule. Idempotent
(existing-tables check), same pattern as p1r2c3h4s5r6_purchase_requests.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'att3nd4nc1b'
down_revision: Union[str, Sequence[str], None] = 'c0llections4a'
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
            sa.Column("center_lat", sa.Float(), nullable=False),
            sa.Column("center_lng", sa.Float(), nullable=False),
            sa.Column("radius_meters", sa.Integer(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "attendance_records" not in existing_tables:
        op.create_table(
            "attendance_records",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("branch_id", sa.String(), sa.ForeignKey("branches.id"), nullable=True),
            sa.Column("record_date", sa.Date(), nullable=False),
            sa.Column("check_in_at", sa.DateTime(), nullable=True),
            sa.Column("check_in_lat", sa.Float(), nullable=True),
            sa.Column("check_in_lng", sa.Float(), nullable=True),
            sa.Column("check_in_in_fence", sa.Boolean(), nullable=True),
            sa.Column("check_in_photo_path", sa.String(), nullable=True),
            sa.Column("out_of_fence_reason", sa.Text(), nullable=True),
            sa.Column("check_out_at", sa.DateTime(), nullable=True),
            sa.Column("check_out_lat", sa.Float(), nullable=True),
            sa.Column("check_out_lng", sa.Float(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "leave_requests" not in existing_tables:
        op.create_table(
            "leave_requests",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("leave_type", sa.String(), nullable=False),
            sa.Column("date_from", sa.Date(), nullable=False),
            sa.Column("date_to", sa.Date(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("approver_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    indexes = {ix["name"] for ix in inspector.get_indexes("attendance_records")}
    if "idx_attendance_user_date" not in indexes:
        op.create_index("idx_attendance_user_date", "attendance_records", ["user_id", "record_date"])
    leave_indexes = {ix["name"] for ix in inspector.get_indexes("leave_requests")}
    if "idx_leave_user_status" not in leave_indexes:
        op.create_index("idx_leave_user_status", "leave_requests", ["user_id", "status"])


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
