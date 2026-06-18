"""add_fms_labels_and_login_tracking

Revision ID: 89f9783a2b2c
Revises: b1c2d3e4f5a6
Create Date: 2026-06-18 16:34:42.076277

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '89f9783a2b2c'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS so this is safe to re-run and works on
    # both PostgreSQL (Render) and SQLite (local dev via migrate.py).
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        bind.execute(sa.text(
            "ALTER TABLE tenant_label_configs ADD COLUMN IF NOT EXISTS fms_s VARCHAR"
        ))
        bind.execute(sa.text(
            "ALTER TABLE tenant_label_configs ADD COLUMN IF NOT EXISTS fms_p VARCHAR"
        ))
        bind.execute(sa.text(
            "ALTER TABLE library_label_bundles ADD COLUMN IF NOT EXISTS fms_s VARCHAR"
        ))
        bind.execute(sa.text(
            "ALTER TABLE library_label_bundles ADD COLUMN IF NOT EXISTS fms_p VARCHAR"
        ))
        bind.execute(sa.text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP"
        ))
    else:
        # SQLite: create_all / migrate.py already handles this
        pass


def downgrade() -> None:
    # Dropping columns in SQLite is not supported; on PostgreSQL we leave them
    # in place to avoid data loss on accidental rollback.
    pass
