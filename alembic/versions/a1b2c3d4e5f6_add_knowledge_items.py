"""add knowledge_items table

Revision ID: k1n2o3w4l5e6
Revises: c97831d82bc5
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = 'k1n2o3w4l5e6'
down_revision = 'c97831d82bc5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS knowledge_items (
                id            TEXT PRIMARY KEY,
                tenant_id     TEXT NOT NULL REFERENCES tenants(id),
                title         TEXT NOT NULL,
                description   TEXT,
                category      TEXT,
                tags          TEXT,
                media_kind    TEXT,
                file_url      TEXT,
                file_name     TEXT,
                file_type     TEXT,
                file_size     INTEGER,
                external_url  TEXT,
                created_by_id TEXT REFERENCES users(id),
                created_at    TIMESTAMP DEFAULT NOW(),
                updated_at    TIMESTAMP DEFAULT NOW(),
                is_deleted    BOOLEAN DEFAULT FALSE
            )
        """))
    else:
        try:
            op.create_table(
                'knowledge_items',
                sa.Column('id',            sa.String(),  primary_key=True),
                sa.Column('tenant_id',     sa.String(),  nullable=False),
                sa.Column('title',         sa.String(),  nullable=False),
                sa.Column('description',   sa.Text()),
                sa.Column('category',      sa.String()),
                sa.Column('tags',          sa.String()),
                sa.Column('media_kind',    sa.String()),
                sa.Column('file_url',      sa.String()),
                sa.Column('file_name',     sa.String()),
                sa.Column('file_type',     sa.String()),
                sa.Column('file_size',     sa.Integer()),
                sa.Column('external_url',  sa.String()),
                sa.Column('created_by_id', sa.String()),
                sa.Column('created_at',    sa.DateTime()),
                sa.Column('updated_at',    sa.DateTime()),
                sa.Column('is_deleted',    sa.Boolean(), default=False),
            )
        except Exception:
            pass


def downgrade() -> None:
    op.drop_table('knowledge_items')
