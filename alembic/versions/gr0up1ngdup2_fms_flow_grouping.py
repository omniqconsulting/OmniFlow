"""fms_flow_grouping: fms_flow_groups table, fms_flows.group_id

Revision ID: gr0up1ngdup2
Revises: cum1sp1itv2
Create Date: 2026-07-13
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'gr0up1ngdup2'
down_revision: Union[str, Sequence[str], None] = 'cum1sp1itv2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _insp():
    return sa.inspect(op.get_bind())


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    existing = {c["name"] for c in _insp().get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def upgrade() -> None:
    if 'fms_flow_groups' not in _insp().get_table_names():
        op.create_table(
            'fms_flow_groups',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column('is_deleted', sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )

    _add_column_if_missing('fms_flows', sa.Column('group_id', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('fms_flows', 'group_id')
    op.drop_table('fms_flow_groups')
