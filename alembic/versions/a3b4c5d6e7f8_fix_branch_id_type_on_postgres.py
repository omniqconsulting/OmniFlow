"""fix legacy integer-typed branch_id columns on postgres (users, departments)

users.branch_id / departments.branch_id are String FKs to branches.id (a UUID
string PK) in the SQLAlchemy model, but on some Postgres databases the column
was created as INTEGER before the app switched to UUID string ids (or via a
stray manual ALTER). The startup "auto-column guard" only ADDS columns that
are missing entirely — it never fixes the type of a column that already
exists — so these legacy INTEGER columns silently persisted and any INSERT
that supplies a real UUID branch_id fails with:
  psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type integer

Revision ID: a3b4c5d6e7f8
Revises: t1a2t3u4n5i6
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa

revision = 'a3b4c5d6e7f8'
down_revision = 't1a2t3u4n5i6'
branch_labels = None
depends_on = None


def _fix_column_type(bind, table, column):
    current_type = bind.execute(sa.text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar()
    if current_type is None:
        return  # column doesn't exist yet — auto-column guard / create_all handles that
    if current_type in ("character varying", "text"):
        return  # already the right kind of type
    bind.execute(sa.text(
        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR "
        f"USING {column}::text"
    ))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        _fix_column_type(bind, "users", "branch_id")
        _fix_column_type(bind, "departments", "branch_id")
    else:
        # SQLite has no rigid column typing — legacy values are stored fine either way
        pass


def downgrade():
    pass
