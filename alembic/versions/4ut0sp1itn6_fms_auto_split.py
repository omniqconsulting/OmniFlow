"""fms_auto_split: split config on stages, split hierarchy fields, split evidence table

Revision ID: 4ut0sp1itn6
Revises: b2r4nch5t0ck
Create Date: 2026-07-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '4ut0sp1itn6'
down_revision: Union[str, Sequence[str], None] = 'b2r4nch5t0ck'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _insp():
    return sa.inspect(op.get_bind())


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    existing = {c["name"] for c in _insp().get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def _create_fk_if_missing(table: str, fk_name: str, referent_table: str, local_cols: list, remote_cols: list) -> None:
    existing = {fk["name"] for fk in _insp().get_foreign_keys(table)}
    if fk_name not in existing:
        with op.batch_alter_table(table) as batch_op:
            batch_op.create_foreign_key(fk_name, referent_table, local_cols, remote_cols)


def upgrade() -> None:
    # --- fms_stages: per-stage opt-in split config ---
    _add_column_if_missing('fms_stages', sa.Column('split_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))
    _add_column_if_missing('fms_stages', sa.Column('split_target_field', sa.String(), nullable=True))
    _add_column_if_missing('fms_stages', sa.Column('split_actual_field', sa.String(), nullable=True))

    # --- library_flow_stages: mirror config on library template stages ---
    _add_column_if_missing('library_flow_stages', sa.Column('split_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))
    _add_column_if_missing('library_flow_stages', sa.Column('split_target_field', sa.String(), nullable=True))
    _add_column_if_missing('library_flow_stages', sa.Column('split_actual_field', sa.String(), nullable=True))

    # --- fms_ticket_splits: hierarchical split-record fields (additive) ---
    _add_column_if_missing('fms_ticket_splits', sa.Column('root_ticket_id', sa.String(), nullable=True))
    _add_column_if_missing('fms_ticket_splits', sa.Column('split_display_id', sa.String(), nullable=True))
    _add_column_if_missing('fms_ticket_splits', sa.Column('split_sequence', sa.Integer(), nullable=True))
    _add_column_if_missing('fms_ticket_splits', sa.Column('split_stage_id', sa.String(), nullable=True))
    _add_column_if_missing('fms_ticket_splits', sa.Column('target_value_at_split', sa.Float(), nullable=True))
    _add_column_if_missing('fms_ticket_splits', sa.Column('entered_value', sa.Float(), nullable=True))
    _add_column_if_missing('fms_ticket_splits', sa.Column('is_remainder', sa.Boolean(), nullable=True, server_default=sa.false()))
    _add_column_if_missing('fms_ticket_splits', sa.Column('is_auto_split', sa.Boolean(), nullable=True, server_default=sa.false()))

    _create_fk_if_missing(
        'fms_ticket_splits', 'fk_fms_ticket_splits_root_ticket_id', 'fms_tickets',
        ['root_ticket_id'], ['id'],
    )
    _create_fk_if_missing(
        'fms_ticket_splits', 'fk_fms_ticket_splits_split_stage_id', 'fms_stages',
        ['split_stage_id'], ['id'],
    )

    # --- fms_split_evidence: new table ---
    if 'fms_split_evidence' not in _insp().get_table_names():
        op.create_table(
            'fms_split_evidence',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False),
            sa.Column('split_id', sa.String(), sa.ForeignKey('fms_ticket_splits.id'), nullable=False),
            sa.Column('file_type', sa.String(), nullable=False),
            sa.Column('file_url', sa.String(), nullable=False),
            sa.Column('file_name', sa.String(), nullable=True),
            sa.Column('uploaded_by', sa.String(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table('fms_split_evidence')

    with op.batch_alter_table('fms_ticket_splits') as batch_op:
        batch_op.drop_constraint('fk_fms_ticket_splits_split_stage_id', type_='foreignkey')
        batch_op.drop_constraint('fk_fms_ticket_splits_root_ticket_id', type_='foreignkey')

    op.drop_column('fms_ticket_splits', 'is_auto_split')
    op.drop_column('fms_ticket_splits', 'is_remainder')
    op.drop_column('fms_ticket_splits', 'entered_value')
    op.drop_column('fms_ticket_splits', 'target_value_at_split')
    op.drop_column('fms_ticket_splits', 'split_stage_id')
    op.drop_column('fms_ticket_splits', 'split_sequence')
    op.drop_column('fms_ticket_splits', 'split_display_id')
    op.drop_column('fms_ticket_splits', 'root_ticket_id')

    op.drop_column('library_flow_stages', 'split_actual_field')
    op.drop_column('library_flow_stages', 'split_target_field')
    op.drop_column('library_flow_stages', 'split_enabled')

    op.drop_column('fms_stages', 'split_actual_field')
    op.drop_column('fms_stages', 'split_target_field')
    op.drop_column('fms_stages', 'split_enabled')
