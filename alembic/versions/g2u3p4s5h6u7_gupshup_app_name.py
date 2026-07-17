"""gupshup_app_name

Revision ID: g2u3p4s5h6u7
Revises: wa9ev1nttggl
Create Date: 2026-07-17 00:00:00.000000

Adds tenants.gupshup_app_name — required as `src.name` on the replacement
`/wa/api/v1/template/msg` Gupshup endpoint after the retired `/sm` endpoint
was swapped out (see app/services/gupshup.py module docstring). Additive
column only, no drops, no data migration needed. Idempotent, same pattern
as d84d69ded1cc / wa9ev1nttggl, since this app's own startup self-heal can
create this column from the model before Alembic ever runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g2u3p4s5h6u7'
down_revision: Union[str, Sequence[str], None] = 'wa9ev1nttggl'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tenants_cols = {c['name'] for c in inspector.get_columns('tenants')}

    with op.batch_alter_table('tenants', schema=None) as batch_op:
        if 'gupshup_app_name' not in tenants_cols:
            batch_op.add_column(sa.Column('gupshup_app_name', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.drop_column('gupshup_app_name')
