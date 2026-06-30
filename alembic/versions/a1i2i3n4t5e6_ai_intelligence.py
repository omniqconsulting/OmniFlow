"""ai_intelligence

Revision ID: a1i2i3n4t5e6
Revises: p1r2i3c4e5m6
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1i2i3n4t5e6'
down_revision: Union[str, Sequence[str], None] = 'p1r2i3c4e5m6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "tier_snapshots" not in existing_tables:
        op.create_table(
            "tier_snapshots",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("entity_type", sa.String(), nullable=False),
            sa.Column("entity_id", sa.String(), nullable=False),
            sa.Column("tier", sa.String(), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("basis_json", sa.Text(), nullable=True),
            sa.Column("period_label", sa.String(), nullable=False),
            sa.Column("computed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "anomaly_alerts" not in existing_tables:
        op.create_table(
            "anomaly_alerts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("alert_type", sa.String(), nullable=False),
            sa.Column("entity_type", sa.String(), nullable=True),
            sa.Column("entity_id", sa.String(), nullable=True),
            sa.Column("entity_label", sa.String(), nullable=True),
            sa.Column("severity", sa.String(), nullable=True, server_default="MEDIUM"),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("metric_json", sa.Text(), nullable=True),
            sa.Column("is_read", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("is_dismissed", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("detected_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)  # refresh after table creation
    ts_indexes = {ix["name"] for ix in inspector.get_indexes("tier_snapshots")}
    if "idx_tier_snapshots_entity" not in ts_indexes:
        op.create_index(
            "idx_tier_snapshots_entity", "tier_snapshots",
            ["tenant_id", "entity_type", "entity_id", sa.text("computed_at DESC")],
        )

    aa_indexes = {ix["name"] for ix in inspector.get_indexes("anomaly_alerts")}
    if "idx_anomaly_alerts_active" not in aa_indexes:
        op.create_index(
            "idx_anomaly_alerts_active", "anomaly_alerts",
            ["tenant_id", "is_dismissed", sa.text("detected_at DESC")],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    for table in ("anomaly_alerts", "tier_snapshots"):
        if table in existing_tables:
            op.drop_table(table)
