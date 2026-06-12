"""Verify Phase 1 DB schema and WS manager module."""
from app.database import create_tables, SessionLocal
create_tables()
from app.library_seeds import seed_library
db = SessionLocal()
seed_library(db)
db.close()

import sqlite3
conn = sqlite3.connect('factoryos.db')
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print("Tables:", tables)
assert "websocket_sessions" in tables, "MISSING: websocket_sessions"
ws = conn.execute("SELECT COUNT(*) FROM websocket_sessions").fetchone()[0]
print("websocket_sessions rows:", ws)
conn.close()

# Verify ws_manager module
from app.ws_manager import (
    manager, ALL_EVENT_TYPES,
    TICKET_ASSIGNED, TICKET_STATUS_CHANGED, TICKET_COMMENTED,
    TICKET_OVERDUE, TICKET_FLAGGED, TICKET_HELP_REQUESTED,
    CHECKLIST_DUE_SOON, CHECKLIST_OVERDUE, CHECKLIST_COMPLETED,
    NOTIFICATION_NEW, FMS_STAGE_TRANSITION, STORE_ALERT,
)
assert len(ALL_EVENT_TYPES) == 12, f"Expected 12 event types, got {len(ALL_EVENT_TYPES)}"
print("Event types (12):", ALL_EVENT_TYPES)
print("OK - Phase 1 schema + ws_manager verified")
