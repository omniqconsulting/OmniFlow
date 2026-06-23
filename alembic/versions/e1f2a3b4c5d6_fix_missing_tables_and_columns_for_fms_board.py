"""fix_missing_tables_and_columns_for_fms_board

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-06-23

Comprehensive fix: every table and column present in database.py models
but absent from PostgreSQL on Render. The FMS board crashes because:

  1. get_linked_entity_options() queries customers / end_products /
     custom_reference_lists / custom_reference_items — none exist on PG.
  2. fms_stages is missing: color, default_assignee_id, sub_module_tag,
     deployed_submodule_id, is_mandatory, completion_note_required,
     is_terminal, allowed_next_stages_json, description — template blows
     up on s.color, s.is_terminal, s.default_assignee_id etc.
  3. fms_tickets is missing: is_flagged, display_id, target_qty,
     qty_unit, wo_number, priority, flagged_reason, completed_at,
     closed_at, created_by_id, updated_at.
  4. fms_stage_history is missing: direction, return_reason,
     completion_note, qty_completed, from_stage_id, from_stage_name,
     evidence_url, evidence_filename, stage_name.
  5. fms_flows is missing: color, library_flow_id,
     library_version_at_deploy, created_by_id, updated_at.
  6. Several whole tables missing: tenant_feature_overrides, login_events,
     tenant_ai_usage, plan_upgrade_requests, customers, end_products,
     custom_reference_lists, custom_reference_items,
     linked_entity_references, custom_submodule_responses.
  7. tenants table missing: is_approved, trial_started_at, ai_custom_limit,
     checklist_notif_hours, ticket_seq.

All ops use IF NOT EXISTS / IF NOT EXIST so re-running is always safe.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1f2a3b4c5d6'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def _pg(bind, stmts):
    """Execute a list of raw SQL strings, each swallowing its own error."""
    for stmt in stmts:
        try:
            bind.execute(sa.text(stmt))
        except Exception as e:
            # Log but never abort — IF NOT EXISTS should prevent most errors
            import logging
            logging.getLogger(__name__).warning("Migration stmt skipped (%s): %s", stmt[:80], e)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return   # SQLite handled by create_all / migrate.py

    # ── 1. tenants — missing columns ─────────────────────────────────────────
    _pg(bind, [
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_approved          BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_started_at     TIMESTAMP",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ai_custom_limit      INTEGER",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS checklist_notif_hours VARCHAR",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ticket_seq           INTEGER DEFAULT 0",
    ])

    # ── 2. fms_flows — missing columns ───────────────────────────────────────
    _pg(bind, [
        "ALTER TABLE fms_flows ADD COLUMN IF NOT EXISTS color                    VARCHAR DEFAULT '#3b82f6'",
        "ALTER TABLE fms_flows ADD COLUMN IF NOT EXISTS library_flow_id          VARCHAR",
        "ALTER TABLE fms_flows ADD COLUMN IF NOT EXISTS library_version_at_deploy INTEGER",
        "ALTER TABLE fms_flows ADD COLUMN IF NOT EXISTS created_by_id            VARCHAR REFERENCES users(id)",
        "ALTER TABLE fms_flows ADD COLUMN IF NOT EXISTS updated_at               TIMESTAMP DEFAULT NOW()",
    ])

    # ── 3. fms_stages — missing columns ──────────────────────────────────────
    _pg(bind, [
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS description              TEXT",
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS color                    VARCHAR DEFAULT '#3b82f6'",
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS default_assignee_id      VARCHAR REFERENCES users(id)",
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS sub_module_tag           VARCHAR",
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS deployed_submodule_id    VARCHAR REFERENCES library_submodule_definitions(id)",
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS is_mandatory             BOOLEAN DEFAULT TRUE",
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS completion_note_required BOOLEAN DEFAULT FALSE",
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS is_terminal              BOOLEAN DEFAULT FALSE",
        "ALTER TABLE fms_stages ADD COLUMN IF NOT EXISTS allowed_next_stages_json TEXT DEFAULT '[]'",
    ])

    # ── 4. fms_tickets — missing columns ─────────────────────────────────────
    _pg(bind, [
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS display_id         VARCHAR",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS wo_number          VARCHAR",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS priority           VARCHAR DEFAULT 'MEDIUM'",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS target_qty         INTEGER",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS qty_unit           VARCHAR",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS is_flagged         BOOLEAN DEFAULT FALSE",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS flagged_reason     VARCHAR",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS completed_at       TIMESTAMP",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS closed_at          TIMESTAMP",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS created_by_id      VARCHAR REFERENCES users(id)",
        "ALTER TABLE fms_tickets ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMP DEFAULT NOW()",
    ])

    # ── 5. fms_stage_history — missing columns ────────────────────────────────
    _pg(bind, [
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS stage_name       VARCHAR",
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS direction         VARCHAR DEFAULT 'FORWARD'",
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS return_reason     TEXT",
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS completion_note   TEXT",
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS qty_completed     INTEGER DEFAULT 0",
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS from_stage_id     VARCHAR",
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS from_stage_name   VARCHAR",
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS evidence_url      VARCHAR",
        "ALTER TABLE fms_stage_history ADD COLUMN IF NOT EXISTS evidence_filename  VARCHAR",
    ])

    # ── 6. Missing whole tables ───────────────────────────────────────────────

    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS tenant_feature_overrides (
            id         VARCHAR PRIMARY KEY,
            tenant_id  VARCHAR NOT NULL REFERENCES tenants(id),
            feature    VARCHAR NOT NULL,
            enabled    BOOLEAN NOT NULL,
            note       VARCHAR,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))

    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS login_events (
            id           VARCHAR PRIMARY KEY,
            tenant_id    VARCHAR NOT NULL REFERENCES tenants(id),
            user_id      VARCHAR NOT NULL REFERENCES users(id),
            logged_in_at TIMESTAMP DEFAULT NOW() NOT NULL
        )
    """))

    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS tenant_ai_usage (
            id         VARCHAR PRIMARY KEY,
            tenant_id  VARCHAR NOT NULL REFERENCES tenants(id),
            date       VARCHAR NOT NULL,
            call_count INTEGER DEFAULT 0
        )
    """))

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


def downgrade():
    pass  # destructive — not supported
