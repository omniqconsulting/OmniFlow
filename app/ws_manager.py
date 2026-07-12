"""
Phase 1 — Real-time Sync Foundation
WebSocket connection manager + broadcast event types (§18.2).

Design principles:
  • Tenant isolation — NEVER broadcast across tenant boundaries.
  • Audience scoping — each event type has a defined audience.
  • Sync bridge — broadcast_sync() can be called from sync route handlers.
  • Fallback — clients that can't hold a WebSocket use GET /api/poll.
end of note
"""
import asyncio
import json
import logging
from typing import Dict, Optional, Set

from fastapi import WebSocket

log = logging.getLogger(__name__)

# ── §18.2 Broadcast Event Types ───────────────────────────────────────────────
# 12 event types defined now so routing logic exists before triggering features
# are built in later phases.

TICKET_ASSIGNED       = "TICKET_ASSIGNED"       # audience: assignee
TICKET_STATUS_CHANGED = "TICKET_STATUS_CHANGED" # audience: admin + manager(team) + assignee
TICKET_COMMENTED      = "TICKET_COMMENTED"      # audience: assignee + helpers + creator
TICKET_OVERDUE        = "TICKET_OVERDUE"        # audience: admin + manager(team) + assignee
TICKET_FLAGGED        = "TICKET_FLAGGED"        # audience: admin + assignee
TICKET_HELP_REQUESTED = "TICKET_HELP_REQUESTED" # audience: admin + manager(team)
CHECKLIST_DUE_SOON    = "CHECKLIST_DUE_SOON"    # audience: assigned user
CHECKLIST_OVERDUE     = "CHECKLIST_OVERDUE"     # audience: admin + manager + assigned user
CHECKLIST_COMPLETED   = "CHECKLIST_COMPLETED"   # audience: admin + manager(team)
NOTIFICATION_NEW      = "NOTIFICATION_NEW"      # audience: specific user (targeted)
FMS_STAGE_TRANSITION  = "FMS_STAGE_TRANSITION"  # Phase 2 — admin + manager(team) + new assignee
STORE_ALERT           = "STORE_ALERT"           # Phase 4 — Store Manager role only
SPLIT_CREATED         = "SPLIT_CREATED"         # FMS Auto-Split Engine — admin + manager(team) + new assignee

ALL_EVENT_TYPES = [
    TICKET_ASSIGNED, TICKET_STATUS_CHANGED, TICKET_COMMENTED,
    TICKET_OVERDUE, TICKET_FLAGGED, TICKET_HELP_REQUESTED,
    CHECKLIST_DUE_SOON, CHECKLIST_OVERDUE, CHECKLIST_COMPLETED,
    NOTIFICATION_NEW, FMS_STAGE_TRANSITION, STORE_ALERT, SPLIT_CREATED,
]


# ── Connection Manager ────────────────────────────────────────────────────────

class WebSocketManager:
    """
    Per-tenant WebSocket connection pool.

    Internal structure:
      _connections[tenant_id][user_id] = set[WebSocket]

    All public methods that touch _connections acquire _lock to prevent
    race conditions when multiple coroutines connect/disconnect simultaneously.
    """

    def __init__(self):
        # {tenant_id: {user_id: set[WebSocket]}}
        self._connections: Dict[str, Dict[str, Set[WebSocket]]] = {}
        self._lock = asyncio.Lock()

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self, ws: WebSocket, tenant_id: str, user_id: str) -> None:
        """Accept the WebSocket and register it in the pool."""
        await ws.accept()
        async with self._lock:
            tenant_pool = self._connections.setdefault(tenant_id, {})
            user_sockets = tenant_pool.setdefault(user_id, set())
            user_sockets.add(ws)
        log.debug("WS connect  tenant=%s user=%s total=%d",
                  tenant_id[:8], user_id[:8], self.connection_count(tenant_id))

    async def disconnect(self, ws: WebSocket, tenant_id: str, user_id: str) -> None:
        """Remove a WebSocket from the pool."""
        async with self._lock:
            tenant_pool = self._connections.get(tenant_id, {})
            user_sockets = tenant_pool.get(user_id, set())
            user_sockets.discard(ws)
            if not user_sockets:
                tenant_pool.pop(user_id, None)
            if not tenant_pool:
                self._connections.pop(tenant_id, None)
        log.debug("WS disconnect tenant=%s user=%s", tenant_id[:8], user_id[:8])

    # ── Sending ───────────────────────────────────────────────────────────────

    async def send_to_user(self, tenant_id: str, user_id: str,
                           event_type: str, data: dict) -> None:
        """Send an event to all open connections for a specific user."""
        payload = json.dumps({"event": event_type, "data": data})
        dead: list[WebSocket] = []
        async with self._lock:
            sockets = set(
                self._connections.get(tenant_id, {}).get(user_id, set())
            )
        for ws in sockets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.get(tenant_id, {}).get(user_id, set()).discard(ws)

    async def send_to_users(self, tenant_id: str, user_ids: list,
                            event_type: str, data: dict) -> None:
        """Broadcast an event to a list of user IDs within one tenant."""
        for uid in set(user_ids):   # deduplicate
            await self.send_to_user(tenant_id, uid, event_type, data)

    async def broadcast_to_tenant(self, tenant_id: str,
                                  event_type: str, data: dict) -> None:
        """Broadcast an event to ALL connected users of a tenant."""
        async with self._lock:
            user_ids = list(self._connections.get(tenant_id, {}).keys())
        await self.send_to_users(tenant_id, user_ids, event_type, data)

    # ── Introspection ─────────────────────────────────────────────────────────

    def get_online_user_ids(self, tenant_id: str) -> list:
        """Return user_ids that currently have at least one open connection."""
        return [
            uid for uid, sockets
            in self._connections.get(tenant_id, {}).items()
            if sockets
        ]

    def connection_count(self, tenant_id: str) -> int:
        return sum(
            len(s) for s in self._connections.get(tenant_id, {}).values()
        )

    def total_connections(self) -> int:
        return sum(
            self.connection_count(tid) for tid in self._connections
        )


# ── Singleton ─────────────────────────────────────────────────────────────────

manager = WebSocketManager()


# ── Sync → Async bridge ───────────────────────────────────────────────────────
# Sync FastAPI route handlers run in a thread pool executor.
# broadcast_sync() schedules an async task on the captured main event loop.

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call once at startup (from an async context) to capture the running loop."""
    global _main_loop
    _main_loop = loop


def broadcast_sync(tenant_id: str, user_ids: list,
                   event_type: str, data: dict) -> None:
    """
    Fire-and-forget WS broadcast from a synchronous route handler.
    Safe to call from any thread; silently no-ops if the loop isn't captured yet.
    """
    if not _main_loop or not _main_loop.is_running():
        return
    try:
        asyncio.run_coroutine_threadsafe(
            manager.send_to_users(tenant_id, user_ids, event_type, data),
            _main_loop,
        )
    except Exception as exc:
        log.debug("broadcast_sync error: %s", exc)
