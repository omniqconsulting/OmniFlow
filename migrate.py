"""
Auto-migration utility for omniflow.db
----------------------------------------
Compares every Column() definition in app/database.py against the live
SQLite schema and issues ALTER TABLE ... ADD COLUMN for anything missing.

Usage (manual):
    python migrate.py

Automatic:
    Called by app.database.create_tables() on every server startup.
    Safe to run repeatedly — it skips columns that already exist.
"""

import sqlite3
import re
import os
import logging

logger = logging.getLogger(__name__)

# SQLAlchemy type → SQLite storage type
_TYPE_MAP = {
    "String":   "TEXT",
    "Text":     "TEXT",
    "Boolean":  "INTEGER",
    "Integer":  "INTEGER",
    "Float":    "REAL",
    "DateTime": "TEXT",
    "Date":     "TEXT",
}

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_DB_FILE      = os.path.join(_PROJECT_ROOT, "omniflow.db")
_MODEL_FILE   = os.path.join(_PROJECT_ROOT, "app", "database.py")


def _parse_model_columns() -> dict[str, dict[str, str]]:
    """Return {table_name: {col_name: sqlite_type}} from database.py."""
    with open(_MODEL_FILE, encoding="utf-8") as f:
        src = f.read()

    table_col_types: dict[str, dict[str, str]] = {}
    current_table: str | None = None

    for line in src.splitlines():
        m = re.search(r'__tablename__\s*=\s*["\'](\w+)["\']', line)
        if m:
            current_table = m.group(1)
            table_col_types[current_table] = {}
            continue

        if current_table:
            m = re.match(r'\s+(\w+)\s*=\s*Column\((\w+)', line)
            if m:
                col, sa_type = m.group(1), m.group(2)
                table_col_types[current_table][col] = _TYPE_MAP.get(sa_type, "TEXT")

    return table_col_types


def run_migrations(db_path: str = _DB_FILE) -> list[str]:
    """
    Apply any missing columns to the live database.
    Returns a list of migration statements that were executed.
    """
    model = _parse_model_columns()
    conn = sqlite3.connect(db_path)
    applied: list[str] = []

    try:
        cur = conn.cursor()
        for table, cols in model.items():
            cur.execute(f"PRAGMA table_info({table})")
            rows = cur.fetchall()
            if not rows:
                # Table doesn't exist yet — create_all() will handle it
                continue

            db_cols = {r[1] for r in rows}
            for col, sqlite_type in cols.items():
                if col not in db_cols:
                    sql = f"ALTER TABLE {table} ADD COLUMN {col} {sqlite_type}"
                    conn.execute(sql)
                    applied.append(sql)
                    logger.info("[migrate] %s", sql)

        conn.commit()
    finally:
        conn.close()

    return applied


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    applied = run_migrations()
    if applied:
        print(f"\n{len(applied)} migration(s) applied:")
        for s in applied:
            print(f"  {s}")
    else:
        print("Schema is up to date — nothing to migrate.")
