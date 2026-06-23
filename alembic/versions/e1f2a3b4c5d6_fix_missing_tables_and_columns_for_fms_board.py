"""fix_missing_tables_and_columns_for_fms_board

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-06-23

Adds every table and column that exists in database.py models but was never
given a migration, causing 500 errors on the FMS board and any route that
touches these tables on PostgreSQL (Render).

Missing tables:
  - tenant_feature_overrides
  - login_events
  - tenant_ai_usage
  - plan_upgrade_requests
  - customers
  - end_products
  - custom_reference_lists
  - custom_reference_items
  - linked_entity_references
  - custom_submodule_responses

Missing columns on tenants:
  - is_approved, trial_started_at, ai_custom_limit,
    checklist_notif_hours, ticket_seq
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1f2a3b4c5d6'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    if bind.dialect.name == 'postgresql':
        # ── 1. Missing columns on existing tables ─────────────────────────────
        cols = [
            "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_approved BOOLEAN DEFAULT TRUE",
            "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMP",
            "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ai_custom_limit INTEGER",
            "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS checklist_notif_hours VARCHAR",
            "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ticket_seq INTEGER DEFAULT 0",
        ]
        for stmt in cols:
            bind.execute(sa.text(stmt))

        # ── 2. tenant_feature_overrides ───────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS tenant_feature_overrides (
                id          VARCHAR PRIMARY KEY,
                tenant_id   VARCHAR NOT NULL REFERENCES tenants(id),
                feature     VARCHAR NOT NULL,
                enabled     BOOLEAN NOT NULL,
                note        VARCHAR,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """))

        # ── 3. login_events ───────────────────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS login_events (
                id           VARCHAR PRIMARY KEY,
                tenant_id    VARCHAR NOT NULL REFERENCES tenants(id),
                user_id      VARCHAR NOT NULL REFERENCES users(id),
                logged_in_at TIMESTAMP DEFAULT NOW() NOT NULL
            )
        """))

        # ── 4. tenant_ai_usage ────────────────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS tenant_ai_usage (
                id          VARCHAR PRIMARY KEY,
                tenant_id   VARCHAR NOT NULL REFERENCES tenants(id),
                date        VARCHAR NOT NULL,
                call_count  INTEGER DEFAULT 0
            )
        """))

        # ── 5. plan_upgrade_requests ──────────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS plan_upgrade_requests (
                id          VARCHAR PRIMARY KEY,
                tenant_id   VARCHAR NOT NULL REFERENCES tenants(id),
                from_plan   VARCHAR NOT NULL,
                to_plan     VARCHAR NOT NULL,
                message     VARCHAR,
                status      VARCHAR DEFAULT 'PENDING',
                created_at  TIMESTAMP DEFAULT NOW(),
                actioned_at TIMESTAMP,
                actioned_by VARCHAR REFERENCES super_admins(id)
            )
        """))

        # ── 6. customers ──────────────────────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS customers (
                id             VARCHAR PRIMARY KEY,
                tenant_id      VARCHAR NOT NULL REFERENCES tenants(id),
                name           VARCHAR NOT NULL,
                contact_person VARCHAR,
                phone          VARCHAR,
                email          VARCHAR,
                address        TEXT,
                notes          TEXT,
                is_active      BOOLEAN DEFAULT TRUE,
                is_deleted     BOOLEAN DEFAULT FALSE,
                created_by_id  VARCHAR REFERENCES users(id),
                created_at     TIMESTAMP DEFAULT NOW(),
                updated_at     TIMESTAMP DEFAULT NOW()
            )
        """))

        # ── 7. end_products ───────────────────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS end_products (
                id            VARCHAR PRIMARY KEY,
                tenant_id     VARCHAR NOT NULL REFERENCES tenants(id),
                name          VARCHAR NOT NULL,
                sku_code      VARCHAR,
                unit          VARCHAR,
                description   TEXT,
                is_active     BOOLEAN DEFAULT TRUE,
                is_deleted    BOOLEAN DEFAULT FALSE,
                created_by_id VARCHAR REFERENCES users(id),
                created_at    TIMESTAMP DEFAULT NOW(),
                updated_at    TIMESTAMP DEFAULT NOW()
            )
        """))

        # ── 8. custom_reference_lists ─────────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS custom_reference_lists (
                id            VARCHAR PRIMARY KEY,
                tenant_id     VARCHAR NOT NULL REFERENCES tenants(id),
                list_name     VARCHAR NOT NULL,
                is_active     BOOLEAN DEFAULT TRUE,
                is_deleted    BOOLEAN DEFAULT FALSE,
                created_by_id VARCHAR REFERENCES users(id),
                created_at    TIMESTAMP DEFAULT NOW()
            )
        """))

        # ── 9. custom_reference_items ─────────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS custom_reference_items (
                id         VARCHAR PRIMARY KEY,
                list_id    VARCHAR NOT NULL REFERENCES custom_reference_lists(id),
                tenant_id  VARCHAR NOT NULL REFERENCES tenants(id),
                value      VARCHAR NOT NULL,
                sort_order INTEGER DEFAULT 0,
                is_active  BOOLEAN DEFAULT TRUE,
                is_deleted BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        # ── 10. linked_entity_references ──────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS linked_entity_references (
                id            VARCHAR PRIMARY KEY,
                tenant_id     VARCHAR NOT NULL REFERENCES tenants(id),
                parent_type   VARCHAR NOT NULL,
                parent_id     VARCHAR NOT NULL,
                entity_type   VARCHAR NOT NULL,
                entity_id     VARCHAR,
                entity_label  VARCHAR,
                custom_text   VARCHAR,
                created_by_id VARCHAR REFERENCES users(id),
                created_at    TIMESTAMP DEFAULT NOW()
            )
        """))

        # ── 11. custom_submodule_responses ────────────────────────────────────
        bind.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS custom_submodule_responses (
                id                      VARCHAR PRIMARY KEY,
                tenant_id               VARCHAR NOT NULL REFERENCES tenants(id),
                submodule_definition_id VARCHAR REFERENCES library_submodule_definitions(id),
                fms_ticket_id           VARCHAR REFERENCES fms_tickets(id),
                stage_history_id        VARCHAR REFERENCES fms_stage_history(id),
                field_responses_json    TEXT,
                submitted_by            VARCHAR REFERENCES users(id),
                created_at              TIMESTAMP DEFAULT NOW()
            )
        """))

    else:
        # SQLite (local dev) — create_all and migrate.py handle these
        pass


def downgrade():
    # Not supported — these tables contain production data
    pass
