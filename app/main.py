from fastapi import FastAPI, Request, Depends, Form, HTTPException, UploadFile, File, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
import asyncio, os, csv, io

from .database import (
    get_db, create_tables,
    SuperAdmin, Tenant, User, Branch, Department,
    TenantFeatureOverride, TenantLabelConfig, PlanUpgradeRequest,
    Ticket, TicketComment, TicketEvent, TicketAssignee,
    ChecklistTemplate, ChecklistAssignment, ChecklistComment,
    Notification, MediaUpload, WebSocketSession,
    FMSFlow, FMSTicket, FMSStageHistory, FMSTicketHelper, FMSEvent,
    TicketStatus, Priority, ChecklistStatus, new_id,
    LoginEvent,
)
from .auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_admin, require_manager,
)
from .notifications import (
    notify_ticket_assigned, notify_ticket_reminder, notify_helper_added,
    notify_ticket_status_changed, notify_ticket_commented,
    notify_ticket_flagged, notify_ticket_help_requested,
    notify_checklist_completed,
)
from .ws_manager import (
    manager as ws_manager, set_main_loop, broadcast_sync,
    TICKET_ASSIGNED, TICKET_STATUS_CHANGED, TICKET_COMMENTED,
    TICKET_FLAGGED, TICKET_HELP_REQUESTED, CHECKLIST_COMPLETED,
)
from .uploads import save_upload
from .analytics import (
    get_employee_kpis, get_org_avg_tat, get_all_employee_kpis,
    get_ticket_volume_chart,
    get_delegation_scorecards, get_delegation_weekly,
    get_delegation_by_dept, get_delegation_by_manager,
    get_delegation_by_priority, get_employee_tat_ranking,
    get_checklist_scorecards, get_checklist_weekly,
    get_checklist_by_template, get_checklist_by_dept,
    get_fms_scorecards, get_fms_flow_summary,
    get_fms_stage_breakdown, get_fms_weekly,
)
from .constants import (
    has_feature, get_limit, within_limit,
    FEATURE_CATALOG, PLAN_LIMITS, PLAN_LABELS, PLAN_ORDER,
    LIMIT_LABELS, feature_label, next_plan, get_plan_features,
)
from .labels import get_labels, DEFAULT_L, INDUSTRY_NAMES, INDUSTRY_PRESETS

app = FastAPI(title="OmniFlow")


# ── Super Admin routers — Phase 0-H / 0-K ────────────────────────────────────
from .superadmin import router as sa_router
from .superadmin_library import router as lib_router
app.include_router(sa_router)
app.include_router(lib_router)
from .fms import router as fms_router
app.include_router(fms_router)
from .submodules import router as submodules_router
app.include_router(submodules_router)
from .ai_router import router as ai_router
app.include_router(ai_router)
from .setup_routes import router as setup_router
app.include_router(setup_router)
from .linked_entities import router as linked_entities_router
app.include_router(linked_entities_router)
from .templates_env import templates, _OrmEncoder, _to_ist, _format_tat  # shared filters
BASE_DIR = os.path.dirname(__file__)

# ── P10-04: Validation helpers ──────────────────────────────────────────────
import re as _re

def _validate_phone(phone: str) -> str | None:
    """Return error message or None if valid. Expects exactly 10 digits."""
    digits = _re.sub(r"\D", "", phone)
    if len(digits) != 10:
        return "Phone must be exactly 10 digits"
    return None

def _validate_email(email: str) -> str | None:
    """Return error message or None if valid (or empty — email is optional)."""
    if not email:
        return None
    if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return "Invalid email address format"
    return None


def _next_employee_id(db, tenant_id: str) -> str:
    """Generate the next EMP-XXXX id for a tenant using MAX to avoid collisions after soft-delete."""
    from sqlalchemy import func as _func
    max_id = db.query(_func.max(User.employee_id)).filter(
        User.tenant_id == tenant_id, User.employee_id.isnot(None)
    ).scalar()
    if max_id and max_id.startswith("EMP-"):
        try:
            next_num = int(max_id[4:]) + 1
        except ValueError:
            next_num = 1
    else:
        next_num = 1
    return f"EMP-{next_num:04d}"

# Default nav feature flags to False — per-route _nav_ctx() overrides with real values.
templates.env.globals["has_inventory"]  = False
templates.env.globals["has_fms"]        = False
templates.env.globals["has_checklists"] = False
_static_dir = os.path.join(BASE_DIR, "static")
os.makedirs(_static_dir, exist_ok=True)
os.makedirs(os.path.join(_static_dir, "uploads"), exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.on_event("startup")
async def startup():
    # Run Alembic migrations so Render/PostgreSQL schema stays current
    try:
        from alembic.config import Config as _AlembicConfig
        from alembic import command as _alembic_cmd
        import os as _os
        _ini = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "alembic.ini")
        _alembic_cfg = _AlembicConfig(_ini)
        _alembic_cmd.upgrade(_alembic_cfg, "head")
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning("Alembic upgrade failed (non-fatal): %s", _e)
    create_tables()
    # ── Auto column guard ─────────────────────────────────────────────────────
    # Introspects every SQLAlchemy model and adds any column that exists in the
    # model but is missing from the live PostgreSQL table.  This is permanently
    # self-maintaining: adding a column to database.py automatically makes it
    # appear on Render after the next deploy — no manual DDL list to update.
    #
    # Rules:
    #   • Only runs on PostgreSQL (SQLite handled by create_all / migrate.py).
    #   • Skips tables that don't exist yet (Alembic migration will create them).
    #   • FK columns are added as plain VARCHAR — the FK constraint is not
    #     enforced here (avoiding ordering issues); Alembic owns constraints.
    #   • Each column is attempted individually so one failure never blocks others.
    #   • Server defaults are applied for Boolean/Integer to avoid NOT NULL errors
    #     on tables that already have rows.
    try:
        import logging as _logging
        from sqlalchemy import inspect as _inspect, text as _text
        from .database import engine as _engine, Base as _Base

        _log = _logging.getLogger(__name__)

        # SA column type → PostgreSQL DDL type (no constraints, keep it simple)
        def _pg_type(col):
            _map = {
                "String":   "VARCHAR",
                "Text":     "TEXT",
                "Boolean":  "BOOLEAN",
                "Integer":  "INTEGER",
                "DateTime": "TIMESTAMP",
                "Float":    "FLOAT",
                "Date":     "DATE",
            }
            return _map.get(type(col.type).__name__, "TEXT")

        # Server-default expressions for types where NULL would break existing rows
        def _pg_default(col):
            t = type(col.type).__name__
            srv = col.server_default
            # Honour explicit server_default if set on the column
            if srv is not None:
                raw = getattr(srv, "arg", None)
                if raw is not None:
                    return f" DEFAULT {raw}"
            # Safe fallbacks by type so existing rows get a value immediately
            if t == "Boolean":
                return " DEFAULT FALSE"
            if t == "Integer":
                return " DEFAULT 0"
            return ""   # VARCHAR / TEXT / TIMESTAMP — NULL is fine

        with _engine.connect() as _conn:
            if _conn.dialect.name == "postgresql":
                _inspector = _inspect(_conn)
                _pg_tables = set(_inspector.get_table_names())

                for _mapper in _Base.registry.mappers:
                    _tname = _mapper.mapped_table.name

                    # Table not yet created (will be handled by Alembic / create_all)
                    if _tname not in _pg_tables:
                        continue

                    _pg_cols = {c["name"] for c in _inspector.get_columns(_tname)}

                    for _col in _mapper.mapped_table.columns:
                        if _col.name in _pg_cols:
                            continue  # already present

                        # FK columns: add as plain VARCHAR; no FK constraint here
                        _dtype = "VARCHAR" if _col.foreign_keys else _pg_type(_col)
                        _dflt  = _pg_default(_col)
                        _stmt  = (
                            f"ALTER TABLE {_tname} "
                            f"ADD COLUMN IF NOT EXISTS {_col.name} {_dtype}{_dflt}"
                        )
                        try:
                            _conn.execute(_text(_stmt))
                            _log.info("Auto-column: added %s.%s (%s%s)",
                                      _tname, _col.name, _dtype, _dflt)
                        except Exception as _col_err:
                            _log.warning("Auto-column skipped %s.%s: %s",
                                         _tname, _col.name, _col_err)

                _conn.commit()
    except Exception as _ce:
        import logging as _logging
        _logging.getLogger(__name__).warning("Auto-column guard failed (non-fatal): %s", _ce)
    # Phase 1-2/3: capture the running event loop for sync→async WS broadcasts
    set_main_loop(asyncio.get_event_loop())
    # Seed Phase 0-K library data (idempotent)
    try:
        from .library_seeds import seed_library
        from .database import SessionLocal
        _db = SessionLocal()
        seed_library(_db)
        _db.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Library seed failed: %s", e)
    try:
        from .scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Scheduler failed to start: %s", e)


@app.on_event("shutdown")
async def shutdown():
    try:
        from .scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


# ── Helper ────────────────────────────────────────────────────────────────────

def redirect(path: str):
    return RedirectResponse(path, status_code=302)

def _L(db, user) -> dict:
    """Return the label dict for the current user's tenant (Phase 0-J)."""
    if user is None:
        return DEFAULT_L
    return get_labels(db, user.tenant_id)

def _limit_hit(tenant, limit_name: str, current_count: int) -> bool:
    """Return True if the plan limit has been reached."""
    return not within_limit(tenant, limit_name, current_count)

def _nav_ctx(db, user, tenant=None) -> dict:
    """Return the three nav feature flags for base.html — avoids repeating per-route."""
    if user is None:
        return {"has_inventory": False, "has_fms": False, "has_checklists": False}
    try:
        t = tenant or db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        return {
            "has_inventory":  has_feature(t, "INVENTORY",  db) if t else False,
            "has_fms":        has_feature(t, "FMS",        db) if t else False,
            # Checklists is a core feature always available on all paid plans
            "has_checklists": True,
        }
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning("_nav_ctx failed: %s", _e)
        return {"has_inventory": False, "has_fms": False, "has_checklists": True}

def _has_inv(db, user) -> bool:
    return _nav_ctx(db, user)["has_inventory"]

def log_event(db, ticket_id, actor_id, event_type, detail=""):
    db.add(TicketEvent(ticket_id=ticket_id, actor_id=actor_id,
                       event_type=event_type, detail=detail))

def _unread_count(db: Session, user: User) -> int:
    return db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False,
    ).count()


def _send_wa_registration_received(phone: str, contact_name: str, company_name: str):
    """Pipeline 5A — omniflow_registration_received. Sends to prospect phone. No mobile_verified gate. Never raises."""
    from .services.msg91 import send_whatsapp_template, normalize_mobile
    if not phone or not phone.strip():
        return
    try:
        send_whatsapp_template(normalize_mobile(phone), "omniflow_registration_received", [contact_name, company_name])
    except Exception:
        import logging
        logging.getLogger("main").exception("_send_wa_registration_received failed for phone=%s", phone)


def _send_wa_registration_alert_sa(company_name: str, contact_name: str, contact_phone: str, tenant_id: str, db):
    """Pipeline 5B — omniflow_registration_alert_sa. Sends to SA_ALERT_PHONE. Never raises."""
    from .services.msg91 import send_whatsapp_template, normalize_mobile
    from .database import WhatsAppMessageLog
    from .constants import SA_ALERT_PHONE
    import json
    if not SA_ALERT_PHONE:
        return
    variables = [company_name, contact_name, contact_phone]
    try:
        ok, error = send_whatsapp_template(normalize_mobile(SA_ALERT_PHONE), "omniflow_registration_alert_sa", variables)
        db.add(WhatsAppMessageLog(
            tenant_id=tenant_id,
            template_name="omniflow_registration_alert_sa",
            recipient_user_id=None,
            recipient_phone=SA_ALERT_PHONE,
            variables_json=json.dumps(variables),
            status="SENT" if ok else "FAILED",
            error_message=error,
            related_entity_type="registration",
            related_entity_id=tenant_id,
        ))
        db.commit()
    except Exception:
        db.rollback()
        import logging
        logging.getLogger("main").exception("_send_wa_registration_alert_sa failed")


def _admin_ids(db: Session, tenant_id: str) -> list:
    """Return user IDs of all ADMIN users in a tenant (for broadcast audience)."""
    return [
        u.id for u in db.query(User).filter(
            User.tenant_id == tenant_id, User.role == "ADMIN",
            User.is_deleted == False, User.is_active == True,
        ).all()
    ]


def _manager_ids_for_ticket(db: Session, tenant_id: str, assignee_id: str) -> list:
    """Return the manager IDs responsible for a ticket's assignee."""
    if not assignee_id:
        return []
    assignee = db.query(User).filter(User.id == assignee_id).first()
    if assignee and assignee.manager_id:
        return [assignee.manager_id]
    return []


# ── Phase 1: WebSocket endpoint (1-1, 1-2, 1-3, 1-4) ─────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    """
    1-1: WebSocket handler.
    1-2: Tenant-scoped — connection is bound to one tenant; no cross-tenant leakage.
    1-3: Authenticated — unauthenticated connections are rejected with code 4001.
    1-4: Session recorded in websocket_sessions table.
    """
    from jose import jwt, JWTError
    from .auth import SECRET_KEY, ALGORITHM

    # 1-3: Authenticate via HTTP-only cookie (sent automatically on same origin)
    token = websocket.cookies.get("token")
    if not token:
        await websocket.close(code=4001, reason="Not authenticated")
        return
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id   = payload["sub"]
        tenant_id = payload["tenant_id"]
    except (JWTError, KeyError):
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Verify user still exists and belongs to this tenant
    user = db.query(User).filter(
        User.id == user_id, User.tenant_id == tenant_id,
        User.is_deleted == False, User.is_active == True,
    ).first()
    if not user:
        await websocket.close(code=4001, reason="User not found")
        return

    # 1-4: Record session in DB
    session_row = WebSocketSession(
        tenant_id=tenant_id, user_id=user_id,
        user_agent=websocket.headers.get("user-agent", "")[:250],
    )
    db.add(session_row)
    db.commit()
    session_id = session_row.id

    # 1-2: Register in tenant-scoped pool
    await ws_manager.connect(websocket, tenant_id, user_id)
    try:
        # Confirm connection to client
        await websocket.send_json({
            "event": "CONNECTED",
            "data": {"user_id": user_id, "tenant_id": tenant_id},
        })
        # Keep-alive loop: client sends "ping", server replies "pong"
        while True:
            try:
                text = await asyncio.wait_for(websocket.receive_text(), timeout=45.0)
                if text == "ping":
                    await websocket.send_text("pong")
                    db.query(WebSocketSession).filter(
                        WebSocketSession.id == session_id
                    ).update({"last_ping": datetime.utcnow()})
                    db.commit()
            except asyncio.TimeoutError:
                # Send a server-side keepalive; client should respond with "ping"
                await websocket.send_json({"event": "PING", "data": {}})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await ws_manager.disconnect(websocket, tenant_id, user_id)
        db.query(WebSocketSession).filter(
            WebSocketSession.id == session_id
        ).delete()
        db.commit()


# ── Phase 1: Fallback polling endpoint (1-5) ─────────────────────────────────

@app.get("/api/poll")
def api_poll(request: Request, since: Optional[str] = None,
             user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """
    1-5: 30-second fallback for clients that cannot maintain a WebSocket
    (poor connection, proxy stripping upgrades, etc.).
    Returns unread notification count + new events since `since` timestamp.
    """
    unread = _unread_count(db, user)
    events = []
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            recent = db.query(Notification).filter(
                Notification.user_id == user.id,
                Notification.created_at > since_dt,
            ).order_by(Notification.created_at).all()
            events = [
                {
                    "event": n.notif_type,
                    "data": {
                        "title": n.title,
                        "body": n.body or "",
                        "link": n.link or "",
                        "unread_count": unread,
                    },
                }
                for n in recent
            ]
        except (ValueError, AttributeError):
            pass
    return JSONResponse({
        "unread_count": unread,
        "events": events,
        "ts": datetime.utcnow().isoformat(),
        "online": ws_manager.connection_count(user.tenant_id),
    })


# ── Live chart data API (for real-time polling) ───────────────────────────────
@app.get("/api/charts/live")
def api_charts_live(
    request: Request,
    date_from: str = "", date_to: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns all chart datasets as JSON. Polled every 30 s by chart pages."""
    tid = user.tenant_id
    tenant = db.query(Tenant).get(tid)
    now = datetime.utcnow()

    _dept_ids    = [v for v in request.query_params.getlist("dept_ids") if v] or None
    _manager_ids = [v for v in request.query_params.getlist("manager_ids") if v] or None

    # delegation
    deleg_wk = get_delegation_weekly(db, tid, _dept_ids, _manager_ids)
    deleg_sc = get_delegation_scorecards(db, tid, date_from or None, date_to or None,
                                          _dept_ids, _manager_ids)
    # checklists
    cl_wk = get_checklist_weekly(db, tid, _dept_ids, _manager_ids)
    cl_sc = get_checklist_scorecards(db, tid, date_from or None, date_to or None,
                                      _dept_ids, _manager_ids)
    # checklist weekly bar chart (done/failed)
    checklist_bars = []
    for i in range(7, -1, -1):
        week_start = (now - timedelta(weeks=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        week_start -= timedelta(days=week_start.weekday())
        week_end = week_start + timedelta(days=7)
        done_c = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.status == "DONE",
            ChecklistAssignment.completed_at >= week_start,
            ChecklistAssignment.completed_at < week_end,
        ).count()
        fail_c = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.status == "FAILED",
            ChecklistAssignment.completed_at >= week_start,
            ChecklistAssignment.completed_at < week_end,
        ).count()
        checklist_bars.append({"label": week_start.strftime("W%W"), "done": done_c, "failed": fail_c})

    # FMS
    has_fms = has_feature(tenant, "FMS", db)
    fms_wk = get_fms_weekly(db, tid) if has_fms else None
    fms_sc = get_fms_scorecards(db, tid, date_from or None, date_to or None) if has_fms else None

    # KPI scorecard numbers for live tile updates
    open_tickets = deleg_sc.get("open", 0)
    closed_tickets = deleg_sc.get("closed", 0)
    cl_compliance = cl_sc.get("compliance", 0) if cl_sc else 0

    return JSONResponse({
        "ts": now.isoformat(),
        "deleg_wk": deleg_wk,
        "deleg_sc": {k: v for k, v in deleg_sc.items() if not isinstance(v, list)},
        "cl_wk": cl_wk,
        "cl_sc": {k: v for k, v in cl_sc.items() if not isinstance(v, list)} if cl_sc else {},
        "checklist_bars": checklist_bars,
        "fms_wk": fms_wk,
        "fms_sc": {k: v for k, v in fms_sc.items() if not isinstance(v, list)} if fms_sc else {},
        "kpis": {
            "open_tickets": open_tickets,
            "closed_tickets": closed_tickets,
            "cl_compliance": cl_compliance,
        },
    })


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    return redirect("/login")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})

@app.post("/login")
def login(request: Request, slug: str = Form(...), phone: str = Form(...),
          password: str = Form(...), db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
    if not tenant:
        return templates.TemplateResponse(request, "login.html", {"error": "Factory not found"})
    if getattr(tenant, "is_suspended", False):
        return templates.TemplateResponse(request, "login.html",
                                          {"error": "This factory account has been suspended. Contact support."})
    user = db.query(User).filter(
        User.tenant_id == tenant.id, User.phone == phone, User.is_deleted == False
    ).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid credentials"})
    user.last_login = datetime.utcnow()
    db.add(LoginEvent(tenant_id=tenant.id, user_id=user.id))
    db.commit()
    token = create_token(user.id, tenant.id, user.role)
    landing = "/dashboard"
    resp = redirect(landing)
    resp.set_cookie("token", token, httponly=True, max_age=86400)
    return resp


@app.get("/check-slug")
def check_slug_public(slug: str, db: Session = Depends(get_db)):
    """Public slug availability check for the self-registration form."""
    from fastapi.responses import JSONResponse as _J
    exists = db.query(Tenant).filter(Tenant.slug == slug).first()
    return _J({"available": exists is None})


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"error": None})

@app.post("/register")
def register(request: Request, factory_name: str = Form(...), slug: str = Form(...),
             name: str = Form(...), phone: str = Form(...), password: str = Form(...),
             contact_email: str = Form(""),
             db: Session = Depends(get_db)):
    if db.query(Tenant).filter(Tenant.slug == slug).first():
        return templates.TemplateResponse(request, "register.html",
                                          {"error": "Factory ID already taken"})
    # Self-registered tenants start as TRIAL + unapproved
    tenant = Tenant(
        name=factory_name, slug=slug,
        plan="TRIAL", is_approved=False,
        contact_name=name, contact_email=contact_email or None,
        trial_started_at=datetime.utcnow(),
    )
    db.add(tenant)
    db.flush()
    user = User(tenant_id=tenant.id, name=name, phone=phone,
                password_hash=hash_password(password), role="ADMIN")
    db.add(user)
    db.commit()
    # Pipeline 5A — registration received WhatsApp to prospect
    _send_wa_registration_received(phone, name, factory_name)
    # Pipeline 5B — registration alert WhatsApp to SA
    _send_wa_registration_alert_sa(factory_name, name, phone, tenant.id, db)
    return templates.TemplateResponse(request, "register_pending.html", {
        "factory_name": factory_name, "slug": slug, "name": name,
    })


@app.get("/api/team-members")
def api_team_members(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return all active users in the same tenant — used by the Help modal dropdown."""
    members = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.is_deleted == False,
        User.id != user.id,
    ).order_by(User.role, User.name).all()
    return JSONResponse([
        {"id": m.id, "name": m.name, "role": m.role.title()}
        for m in members
    ])


@app.post("/help-request")
async def submit_help_request(
    title: str = Form(...),
    description: str = Form(""),
    assignee_id: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from datetime import timedelta
    # Resolve assignee: use selected person, fallback to first admin, fallback to self
    assignee = None
    if assignee_id:
        assignee = db.query(User).filter(
            User.id == assignee_id,
            User.tenant_id == user.tenant_id,
            User.is_deleted == False,
        ).first()
    if not assignee:
        assignee = db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.role == "ADMIN",
            User.is_deleted == False,
        ).first() or user
    ticket = Ticket(
        tenant_id=user.tenant_id,
        title=title,
        description=description or "(no description)",
        priority="HIGH",
        created_by_id=user.id,
        current_assignee_id=assignee.id,
        due_at=datetime.utcnow() + timedelta(hours=24),
        ticket_type="D",
    )
    if hasattr(ticket, "ticket_category"):
        ticket.ticket_category = "HELP"
    db.add(ticket)
    db.flush()
    tenant = db.query(Tenant).get(user.tenant_id)
    tenant.ticket_seq = (tenant.ticket_seq or 0) + 1
    ticket.display_id = f"T-{tenant.ticket_seq:04d}"
    db.commit()
    return JSONResponse({"ok": True, "display_id": ticket.display_id, "assignee": assignee.name})


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    unread = _unread_count(db, user)
    return templates.TemplateResponse(request, "help.html", {
        "user": user, "unread": unread, "L": _L(db, user),
        **_nav_ctx(db, user),
    })


@app.get("/logout")
def logout():
    resp = redirect("/login")
    resp.delete_cookie("token")
    return resp


# ── Profile & Password Change ──────────────────────────────────────────────────

@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    unread = _unread_count(db, user)
    msg = request.query_params.get("msg", "")
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(request, "profile.html", {
        "user": user, "unread": unread, "L": _L(db, user),
        "msg": msg, "error": error,
        **_nav_ctx(db, user),
    })


@app.post("/profile/update")
def profile_update(
    name: str = Form(...),
    phone: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Check phone not taken by another user in the same tenant
    existing = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.phone == phone,
        User.id != user.id,
        User.is_deleted == False,
    ).first()
    if existing:
        return redirect("/profile?error=Phone+number+already+in+use+by+another+account")
    user.name = name.strip()
    user.phone = phone.strip()
    db.commit()
    return redirect("/profile?msg=Profile+updated+successfully")


@app.post("/profile/change-password")
def profile_change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        return redirect("/profile?error=Current+password+is+incorrect")
    if new_password != confirm_password:
        return redirect("/profile?error=New+passwords+do+not+match")
    if len(new_password) < 6:
        return redirect("/profile?error=Password+must+be+at+least+6+characters")
    user.password_hash = hash_password(new_password)
    db.commit()
    return redirect("/profile?msg=Password+changed+successfully")


# ── Plan & Feature Flags (tenant-facing) — Phase 0-I ──────────────────────────

@app.get("/plan", response_class=HTMLResponse)
def plan_page(request: Request, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    if user.role != "ADMIN":
        raise HTTPException(403, "Admin only")
    tenant = db.query(Tenant).get(user.tenant_id)
    unread = _unread_count(db, user)
    L = _L(db, user)

    # Current usage counts
    user_count    = db.query(User).filter(User.tenant_id == tenant.id,
                                          User.is_deleted == False).count()
    branch_count  = db.query(Branch).filter(Branch.tenant_id == tenant.id,
                                             Branch.is_deleted == False).count()
    from .database import ChecklistTemplate
    cl_count      = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.tenant_id == tenant.id,
        ChecklistTemplate.is_deleted == False).count()
    open_tickets  = db.query(Ticket).filter(
        Ticket.tenant_id == tenant.id,
        Ticket.is_deleted == False,
        Ticket.status.notin_(["CLOSED", "DONE"])).count()

    fms_flow_count = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tenant.id,
        FMSFlow.is_deleted == False,
    ).count()
    usage = {
        "max_users":               user_count,
        "max_branches":            branch_count,
        "max_checklist_templates": cl_count,
        "max_tickets_open":        open_tickets,
        "max_fms_flows":           fms_flow_count,
    }

    # Per-tenant overrides
    overrides = {
        o.feature: o.enabled
        for o in db.query(TenantFeatureOverride).filter(
            TenantFeatureOverride.tenant_id == tenant.id).all()
    }

    # Build feature rows grouped by category
    from collections import defaultdict
    by_category = defaultdict(list)
    for fname, (label, category, min_plan) in FEATURE_CATALOG.items():
        active = has_feature(tenant, fname, db)
        overridden = fname in overrides
        by_category[category].append({
            "name": fname, "label": label,
            "min_plan": min_plan, "active": active,
            "overridden": overridden,
        })

    return templates.TemplateResponse(request, "plan.html", {
        "user": user, "tenant": tenant, "unread": unread, "L": L,
        "by_category": dict(by_category),
        "usage": usage,
        **_nav_ctx(db, user),
        "plan_limits": PLAN_LIMITS,
        "plan_labels": PLAN_LABELS,
        "plan_order": PLAN_ORDER,
        "limit_labels": LIMIT_LABELS,
        "next_plan": next_plan(tenant.plan or "STARTER"),
        "can_export": has_feature(tenant, "CSV_EXPORT", db),
        "now": datetime.utcnow(),
    })


@app.post("/plan/upgrade-request")
def plan_upgrade_request(
    to_plan: str = Form(...),
    message: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tenant admin requests a plan upgrade — creates a record visible in the SA portal."""
    if user.role != "ADMIN":
        raise HTTPException(403, "Admin only")
    tenant = db.query(Tenant).get(user.tenant_id)
    # Prevent duplicate pending requests for the same plan
    existing = db.query(PlanUpgradeRequest).filter(
        PlanUpgradeRequest.tenant_id == tenant.id,
        PlanUpgradeRequest.to_plan == to_plan,
        PlanUpgradeRequest.status == "PENDING",
    ).first()
    if not existing:
        req = PlanUpgradeRequest(
            tenant_id=tenant.id,
            from_plan=tenant.plan or "STARTER",
            to_plan=to_plan,
            message=message.strip() or None,
        )
        db.add(req)
        db.commit()
    return redirect("/plan?msg=upgrade_requested")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/pending", response_class=HTMLResponse)
def pending_approval(request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    tenant = db.query(Tenant).get(user.tenant_id)
    return templates.TemplateResponse(request, "pending_approval.html",
                                      {"user": user, "tenant": tenant, "unread": 0,
                                       "L": _L(db, user)})


def _calc_summary_kpis(db, tid, date_from_str, date_to_str, dept_ids=None, manager_ids=None, dept_name=None):
    """Compute lightweight KPIs for the Summary dashboard view."""
    from datetime import date as _date, datetime as _dt
    try:
        df = _dt.fromisoformat(date_from_str)
        dt = _dt.fromisoformat(date_to_str).replace(hour=23, minute=59, second=59)
    except Exception:
        today = _date.today()
        df = _dt.combine(today - timedelta(days=30), _dt.min.time())
        dt = _dt.combine(today, _dt.max.time())

    q = db.query(Ticket).filter(
        Ticket.tenant_id == tid, Ticket.is_deleted == False)
    from .analytics import _resolve_filter_uids as _rfu
    # Expand dept_name into IDs for the list-based resolver
    _dept_ids = list(dept_ids or [])
    if dept_name and not _dept_ids:
        _dept_ids = [d.id for d in db.query(Department).filter(
            Department.tenant_id == tid, Department.name == dept_name,
            Department.is_deleted == False).all()]
    scoped_uids = _rfu(db, tid, _dept_ids or None, manager_ids or None)
    if scoped_uids is not None:
        q = q.filter(Ticket.current_assignee_id.in_(scoped_uids))

    open_statuses   = ("OPEN", "ACKNOWLEDGED", "IN_PROGRESS")
    closed_statuses = ("DONE", "CLOSED")

    in_period    = q.filter(Ticket.created_at >= df, Ticket.created_at <= dt).all()
    total_open   = sum(1 for t in in_period if t.status in open_statuses)
    prev_open    = total_open  # no prev-period comparison for now
    closed_in_p  = [t for t in in_period if t.status in closed_statuses]
    total_closed = len(closed_in_p)

    # On-time: closed at or before due_at (use closed_at, not updated_at)
    on_time = [t for t in closed_in_p if t.due_at and t.closed_at and t.closed_at <= t.due_at]
    on_time_pct   = round(len(on_time) / max(len(closed_in_p), 1) * 100)
    closed_count  = len(closed_in_p)

    # Avg TaT in hours (created → closed, not created → updated)
    tats = []
    for t in closed_in_p:
        if t.created_at and t.closed_at:
            tats.append((t.closed_at - t.created_at).total_seconds() / 3600)
    avg_tat_hours = round(sum(tats) / max(len(tats), 1), 1) if tats else 0

    # Help tickets open
    open_help = db.query(Ticket).filter(
        Ticket.tenant_id == tid, Ticket.is_deleted == False,
        Ticket.ticket_category == "HELP",
        Ticket.status.in_(open_statuses),
    ).count() if hasattr(Ticket, "ticket_category") else 0

    # Checklist compliance — exclude soft-deleted assignments and deleted templates
    try:
        from .database import ChecklistAssignment as _CA, ChecklistTemplate as _CT
        _active_tmpl_ids = [t.id for t in db.query(_CT.id).filter(
            _CT.tenant_id == tid, _CT.is_deleted == False).all()]
        cl_due  = db.query(_CA).filter(
            _CA.tenant_id == tid, _CA.is_deleted == False,
            _CA.template_id.in_(_active_tmpl_ids),
            _CA.due_at >= df, _CA.due_at <= dt).count()
        cl_done = db.query(_CA).filter(
            _CA.tenant_id == tid, _CA.is_deleted == False,
            _CA.template_id.in_(_active_tmpl_ids),
            _CA.due_at >= df, _CA.due_at <= dt,
            _CA.status == "DONE").count()
        cl_compliance_pct = round(cl_done / max(cl_due, 1) * 100)
    except Exception:
        cl_due = cl_done = cl_compliance_pct = 0

    # FMS
    try:
        from .database import FMSTicket as _FT
        fms_active = db.query(_FT).filter(
            _FT.tenant_id == tid, _FT.is_deleted == False,
            _FT.status.notin_(["COMPLETED", "CLOSED"])).count()
        fms_tat_breaches = 0  # expensive to compute inline; keep 0 for now
    except Exception:
        fms_active = fms_tat_breaches = 0

    class _KPIs:
        pass
    k = _KPIs()
    k.total_open = total_open
    k.prev_open  = prev_open
    k.total_closed = total_closed
    k.total_count  = total_open + total_closed
    k.on_time_pct = on_time_pct
    k.on_time_count = len(on_time)
    k.closed_count = closed_count
    k.avg_tat_hours = avg_tat_hours
    k.open_help = open_help
    k.cl_compliance_pct = cl_compliance_pct
    k.cl_done = cl_done
    k.cl_due  = cl_due
    k.cl_missed = max(0, cl_due - cl_done)
    # Checklist on-time: assignments completed before their due_at
    try:
        from .database import ChecklistAssignment as _CA2
        cl_on_time = db.query(_CA2).filter(
            _CA2.tenant_id == tid, _CA2.due_at >= df, _CA2.due_at <= dt,
            _CA2.status == "DONE", _CA2.completed_at <= _CA2.due_at).count()
    except Exception:
        cl_on_time = 0
    k.cl_on_time = cl_on_time
    k.fms_active = fms_active
    k.fms_tat_breaches = fms_tat_breaches
    # FMS completed in period
    try:
        from .database import FMSTicket as _FT2
        fms_completed = db.query(_FT2).filter(
            _FT2.tenant_id == tid, _FT2.is_deleted == False,
            _FT2.status.in_(["COMPLETED", "CLOSED"]),
            _FT2.updated_at >= df, _FT2.updated_at <= dt).count()
        fms_on_time = db.query(_FT2).filter(
            _FT2.tenant_id == tid, _FT2.is_deleted == False,
            _FT2.status.in_(["COMPLETED", "CLOSED"]),
            _FT2.updated_at >= df, _FT2.updated_at <= dt,
            _FT2.due_at != None, _FT2.updated_at <= _FT2.due_at).count()
    except Exception:
        fms_completed = fms_on_time = 0
    k.fms_completed = fms_completed
    k.fms_on_time = fms_on_time
    k.fms_total = fms_active + fms_completed
    return k


def _calc_dept_health(db, tid, date_from_str, date_to_str):
    """Per-department on-time completion rate for the dept health strip."""
    from datetime import datetime as _dt
    try:
        df = _dt.fromisoformat(date_from_str)
        dt = _dt.fromisoformat(date_to_str).replace(hour=23, minute=59, second=59)
    except Exception:
        return []

    depts = db.query(Department).filter(
        Department.tenant_id == tid, Department.is_deleted == False).all()
    result = []
    for d in depts:
        dept_users = [u.id for u in db.query(User).filter(
            User.tenant_id == tid, User.department_id == d.id,
            User.is_deleted == False).all()]
        if not dept_users:
            continue
        closed = db.query(Ticket).filter(
            Ticket.tenant_id == tid, Ticket.is_deleted == False,
            Ticket.current_assignee_id.in_(dept_users),
            Ticket.status.in_(("DONE", "CLOSED")),
            Ticket.created_at >= df, Ticket.created_at <= dt).all()
        if not closed:
            continue
        on_time = sum(1 for t in closed if t.due_at and t.closed_at and t.closed_at <= t.due_at)
        rate = round(on_time / len(closed) * 100)
        result.append({"dept_id": d.id, "name": d.name, "rate": rate})
    return sorted(result, key=lambda x: x["rate"])


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    tid = user.tenant_id

    tenant = db.query(Tenant).get(tid)
    if not getattr(tenant, "is_approved", True):
        return redirect("/pending")

    unread = _unread_count(db, user)

    if user.role in ("ADMIN", "MANAGER"):
        from datetime import date as _date
        _today = _date.today()
        date_from   = request.query_params.get("date_from") or (_today - timedelta(days=30)).isoformat()
        date_to     = request.query_params.get("date_to") or _today.isoformat()
        dept_ids    = [v for v in request.query_params.getlist("dept_ids") if v] or None
        dept_name   = request.query_params.get("dept_name", None) or None
        manager_ids = [v for v in request.query_params.getlist("manager_ids") if v] or None
        expand_flow = request.query_params.get("expand_flow", None) or None
        view        = request.query_params.get("view", "summary")

        # Managers locked to their own team
        if user.role == "MANAGER":
            manager_ids = [user.id]

        # Distinct department names (avoid duplicates across branches)
        all_depts = db.query(Department).filter(
            Department.tenant_id == tid, Department.is_deleted == False).all()
        _seen_names: set = set()
        departments: list = []
        for _d in sorted(all_depts, key=lambda x: x.name):
            if _d.name not in _seen_names:
                _seen_names.add(_d.name)
                departments.append(_d)

        managers = db.query(User).filter(
            User.tenant_id == tid, User.role.in_(["ADMIN", "MANAGER"]),
            User.is_deleted == False).all() if user.role == "ADMIN" else []
        branches = db.query(Branch).filter(
            Branch.tenant_id == tid, Branch.is_deleted == False).all() \
            if hasattr(Branch, "is_deleted") else \
            db.query(Branch).filter(Branch.tenant_id == tid).all()
        branch_id = request.query_params.get("branch_id", None) or None

        has_fms        = has_feature(tenant, "FMS", db) if hasattr(tenant, "plan") else True
        has_checklists = True  # always available

        # ── Summary View (default) ────────────────────────────────────────────
        if view != "detailed":
            # Date presets
            from datetime import date as _dt2
            _td = _dt2.today()
            date_presets = [
                ("Today",   _td.isoformat(),                        _td.isoformat()),
                ("7d",      (_td - timedelta(days=7)).isoformat(),  _td.isoformat()),
                ("30d",     (_td - timedelta(days=30)).isoformat(), _td.isoformat()),
                ("90d",     (_td - timedelta(days=90)).isoformat(), _td.isoformat()),
            ]
            # Determine active preset
            active_preset = None
            for label, f, t in date_presets:
                if date_from == f and date_to == t:
                    active_preset = label
                    break

            kpis       = _calc_summary_kpis(db, tid, date_from, date_to, dept_ids, manager_ids, dept_name)
            dept_health= _calc_dept_health(db, tid, date_from, date_to)

            # ── Summary Performance Score ─────────────────────────────────────
            sum_perf_components: list = []
            if kpis.total_count > 0:
                sum_perf_components.append({"label": "Ticket On-Time Rate", "value": kpis.on_time_pct, "color": "#3b82f6"})
            if kpis.cl_due > 0:
                sum_perf_components.append({"label": "Checklist Compliance", "value": kpis.cl_compliance_pct, "color": "#10b981"})
            if has_fms:
                fms_on_time_pct = round(kpis.fms_on_time / kpis.fms_completed * 100) if kpis.fms_completed > 0 else 0
                sum_perf_components.append({"label": "FMS On-Time Rate", "value": fms_on_time_pct, "color": "#8b5cf6"})
            sum_perf_score = round(sum(c["value"] for c in sum_perf_components) / len(sum_perf_components)) if sum_perf_components else 0

            return templates.TemplateResponse(request, "dashboard_summary.html", {
                "user": user, "unread": unread, "L": _L(db, user),
                "now": datetime.utcnow(),
                **_nav_ctx(db, user, tenant=tenant),
                "date_from": date_from, "date_to": date_to,
                "dept_ids": dept_ids or [], "dept_name": dept_name, "manager_ids": manager_ids or [],
                "branch_id": branch_id,
                "departments": departments, "managers": managers, "branches": branches,
                "date_presets": date_presets, "active_preset": active_preset,
                "kpis": kpis, "dept_health": dept_health,
                "has_fms": has_fms, "has_checklists": has_checklists,
                "perf_score": sum_perf_score, "perf_components": sum_perf_components,
            })

        # ── Detailed View ─────────────────────────────────────────────────────
        # Delegation
        deleg_sc   = get_delegation_scorecards(db, tid, date_from, date_to, dept_ids, manager_ids)
        deleg_wk   = get_delegation_weekly(db, tid, dept_ids, manager_ids)
        deleg_dept = get_delegation_by_dept(db, tid, date_from, date_to)
        deleg_mgr  = get_delegation_by_manager(db, tid, date_from, date_to) if user.role == "ADMIN" else []
        deleg_pri  = get_delegation_by_priority(db, tid, dept_ids, manager_ids)
        emp_tat    = get_employee_tat_ranking(db, tid, date_from, date_to, dept_ids, manager_ids)

        # Flagged tickets (scoped to manager's team — 'ever worked on')
        ticket_q = db.query(Ticket).filter(
            Ticket.tenant_id == tid, Ticket.is_deleted == False)
        if user.role == "MANAGER":
            mgr_team_ids = [u.id for u in db.query(User).filter(
                User.manager_id == user.id, User.is_deleted == False).all()]
            mgr_team_ids.append(user.id)
            mgr_helper_tids = [h.ticket_id for h in db.query(TicketAssignee).filter(
                TicketAssignee.user_id.in_(mgr_team_ids)).all()]
            ticket_q = ticket_q.filter(
                (Ticket.current_assignee_id.in_(mgr_team_ids)) |
                (Ticket.created_by_id.in_(mgr_team_ids)) |
                (Ticket.id.in_(mgr_helper_tids))
            )
        flagged = ticket_q.filter(Ticket.is_flagged == True).all()

        # Checklists
        cl_sc   = get_checklist_scorecards(db, tid, date_from, date_to, dept_ids, manager_ids)
        cl_wk   = get_checklist_weekly(db, tid, dept_ids, manager_ids)
        cl_tmpl = get_checklist_by_template(db, tid, date_from, date_to)
        cl_dept = get_checklist_by_dept(db, tid, date_from, date_to)

        # FMS
        fms_sc      = get_fms_scorecards(db, tid, date_from, date_to) if has_fms else None
        fms_flows   = get_fms_flow_summary(db, tid) if has_fms else []
        fms_wk      = get_fms_weekly(db, tid) if has_fms else None
        fms_stage_bd= get_fms_stage_breakdown(db, expand_flow, tid) \
                      if (has_fms and expand_flow) else None

        # ── Overall Performance Score (same metrics as summary page) ─────────
        _sk = _calc_summary_kpis(db, tid, date_from, date_to, dept_ids, manager_ids)
        perf_components: list = []
        if _sk.total_count > 0:
            perf_components.append({"label": "Ticket On-Time Rate",   "value": _sk.on_time_pct,       "color": "#3b82f6"})
        if _sk.cl_due > 0:
            perf_components.append({"label": "Checklist Compliance",  "value": _sk.cl_compliance_pct, "color": "#10b981"})
        if has_fms:
            perf_components.append({"label": "FMS On-Time Rate", "value": round(_sk.fms_on_time / _sk.fms_completed * 100) if _sk.fms_completed > 0 else 0, "color": "#8b5cf6"})
        perf_score = round(sum(c["value"] for c in perf_components) / len(perf_components)) if perf_components else 0

        return templates.TemplateResponse(request, "dashboard.html", {
            "user": user, "unread": unread, "L": _L(db, user),
            "now": datetime.utcnow(),
            "timedelta": timedelta,
            "can_export": has_feature(tenant, "CSV_EXPORT", db),
            **_nav_ctx(db, user, tenant=tenant),
            # Filters
            "date_from": date_from, "date_to": date_to,
            "dept_ids": dept_ids or [],
            "manager_ids": manager_ids or [], "expand_flow": expand_flow,
            "departments": departments, "managers": managers,
            # Delegation
            "deleg_sc": deleg_sc, "deleg_wk": deleg_wk,
            "deleg_dept": deleg_dept, "deleg_mgr": deleg_mgr,
            "deleg_pri": deleg_pri, "emp_tat": emp_tat, "flagged": flagged,
            # Checklists
            "cl_sc": cl_sc, "cl_wk": cl_wk,
            "cl_tmpl": cl_tmpl, "cl_dept": cl_dept,
            # FMS
            "has_fms": has_fms, "fms_sc": fms_sc,
            "fms_flows": fms_flows, "fms_wk": fms_wk, "fms_stage_bd": fms_stage_bd,
            # Performance score
            "perf_score": perf_score, "perf_components": perf_components,
        })
    else:  # EMPLOYEE
        # ── KPIs ───────────────────────────────────────────────────────────────
        kpis    = get_employee_kpis(db, user.id, tid)
        org_avg = get_org_avg_tat(db, tid)

        # ── Regular tickets — 'ever worked on' ─────────────────────────────────
        helper_ticket_ids = [
            h.ticket_id for h in db.query(TicketAssignee).filter(
                TicketAssignee.user_id == user.id).all()
        ]
        all_my_tickets = db.query(Ticket).filter(
            Ticket.tenant_id == tid,
            Ticket.is_deleted == False,
            (
                (Ticket.current_assignee_id == user.id) |
                (Ticket.created_by_id == user.id) |
                (Ticket.id.in_(helper_ticket_ids))
            ),
        ).order_by(Ticket.created_at.desc()).all()

        active_tickets = [t for t in all_my_tickets if t.status not in ("DONE", "CLOSED")]
        recent_closed  = [t for t in all_my_tickets if t.status in ("DONE", "CLOSED")][:5]

        # ── FMS tickets — 'ever worked on' ─────────────────────────────────────
        fms_hist_tids = [
            h.ticket_id for h in db.query(FMSStageHistory).filter(
                FMSStageHistory.assignee_id == user.id).all()
        ]
        fms_helper_tids = [
            h.ticket_id for h in db.query(FMSTicketHelper).filter(
                FMSTicketHelper.user_id == user.id).all()
        ]
        all_fms_ids = set(fms_hist_tids) | set(fms_helper_tids)
        my_fms_tickets = db.query(FMSTicket).filter(
            FMSTicket.tenant_id == tid,
            FMSTicket.is_deleted == False,
            (
                (FMSTicket.current_assignee_id == user.id) |
                (FMSTicket.id.in_(all_fms_ids))
            ),
        ).order_by(FMSTicket.updated_at.desc()).all()

        active_fms   = [t for t in my_fms_tickets if t.status not in ("COMPLETED", "CLOSED")]
        complete_fms = [t for t in my_fms_tickets if t.status in ("COMPLETED", "CLOSED")][:5]

        # ── Checklists ─────────────────────────────────────────────────────────
        my_checklists = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.user_id == user.id,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
        ).order_by(ChecklistAssignment.due_at).all()

        # ── Employee Performance Score ────────────────────────────────────────
        _has_fms = has_feature(tenant, "FMS", db) if hasattr(tenant, "plan") else True
        emp_perf_components: list = []
        emp_perf_components.append({"label": "Ticket On-Time Rate",  "value": int(kpis.get("on_time_rate", 0)),  "color": "#3b82f6"})
        emp_perf_components.append({"label": "Checklist Compliance", "value": int(kpis.get("compliance_rate", 0)), "color": "#10b981"})
        if _has_fms:
            _fms_ontime = sum(1 for t in complete_fms if t.due_at and t.updated_at and t.updated_at <= t.due_at) if complete_fms else 0
            _fms_pct = round(_fms_ontime / len(complete_fms) * 100) if complete_fms else 0
            emp_perf_components.append({"label": "FMS On-Time Rate", "value": _fms_pct, "color": "#8b5cf6"})
        emp_perf_score = round(sum(c["value"] for c in emp_perf_components) / len(emp_perf_components))

        return templates.TemplateResponse(request, "employee_dashboard.html", {
            "user": user, "unread": unread, "L": _L(db, user),
            "now": datetime.utcnow(),
            **_nav_ctx(db, user),
            # KPIs
            "kpis": kpis, "org_avg": org_avg,
            # Tickets
            "active_tickets": active_tickets,
            "recent_closed": recent_closed,
            # FMS
            "active_fms": active_fms,
            "complete_fms": complete_fms,
            "has_fms": _has_fms,
            # Checklists
            "my_checklists": my_checklists,
            # Performance score
            "perf_score": emp_perf_score, "perf_components": emp_perf_components,
        })


# ── Tickets ───────────────────────────────────────────────────────────────────

@app.get("/tickets", response_class=HTMLResponse)
def tickets_list(request: Request, status: str = "OPEN", view: str = "table",
                 dept_id: List[str] = Query([]), manager_id: List[str] = Query([]),
                 branch_id: List[str] = Query([]), priority: List[str] = Query([]),
                 ticket_category: List[str] = Query([]),
                 date_from: str = "", date_to: str = "",
                 assignee_id: List[str] = Query([]),
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    from datetime import date as _date
    tid = user.tenant_id
    q = db.query(Ticket).filter(Ticket.tenant_id == tid, Ticket.is_deleted == False)

    if user.role == "MANAGER":
        team_ids = [u.id for u in db.query(User).filter(
            User.manager_id == user.id, User.is_deleted == False).all()]
        team_ids.append(user.id)
        helper_tids = [h.ticket_id for h in db.query(TicketAssignee).filter(
            TicketAssignee.user_id.in_(team_ids)).all()]
        q = q.filter(
            (Ticket.current_assignee_id.in_(team_ids)) |
            (Ticket.created_by_id.in_(team_ids)) |
            (Ticket.id.in_(helper_tids))
        )
    elif user.role == "EMPLOYEE":
        helper_ticket_ids = [h.ticket_id for h in db.query(TicketAssignee).filter(
            TicketAssignee.user_id == user.id).all()]
        q = q.filter(
            (Ticket.current_assignee_id == user.id) |
            (Ticket.id.in_(helper_ticket_ids))
        )

    # Status tab filter — default OPEN
    if status:
        q = q.filter(Ticket.status == status)

    # Extended filters — all params are now List[str]
    if dept_id:
        dept_user_ids = [u.id for u in db.query(User).filter(
            User.department_id.in_(dept_id), User.tenant_id == tid,
            User.is_deleted == False).all()]
        q = q.filter(Ticket.current_assignee_id.in_(dept_user_ids))
    if manager_id:
        mgr_team_ids = []
        for mid in manager_id:
            mgr_team_ids += [u.id for u in db.query(User).filter(
                User.manager_id == mid, User.tenant_id == tid,
                User.is_deleted == False).all()]
        if mgr_team_ids:
            q = q.filter(Ticket.current_assignee_id.in_(mgr_team_ids))
    if branch_id:
        branch_user_ids = [u.id for u in db.query(User).filter(
            User.branch_id.in_(branch_id), User.tenant_id == tid,
            User.is_deleted == False).all()]
        q = q.filter(Ticket.current_assignee_id.in_(branch_user_ids))
    if priority:
        q = q.filter(Ticket.priority.in_(priority))
    if ticket_category and hasattr(Ticket, "ticket_category"):
        q = q.filter(Ticket.ticket_category.in_(ticket_category))
    if assignee_id:
        q = q.filter(Ticket.current_assignee_id.in_(assignee_id))
    if date_from:
        try:
            q = q.filter(Ticket.created_at >= datetime.fromisoformat(date_from))
        except Exception:
            pass
    if date_to:
        try:
            q = q.filter(Ticket.created_at <= datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59))
        except Exception:
            pass

    tickets = q.order_by(Ticket.created_at.desc()).all()

    # Count per status for tab badges
    base_q = db.query(Ticket).filter(Ticket.tenant_id == tid, Ticket.is_deleted == False)
    if user.role == "MANAGER":
        base_q = base_q.filter(
            (Ticket.current_assignee_id.in_(team_ids)) |
            (Ticket.created_by_id.in_(team_ids)) |
            (Ticket.id.in_(helper_tids))
        )
    elif user.role == "EMPLOYEE":
        base_q = base_q.filter(
            (Ticket.current_assignee_id == user.id) |
            (Ticket.id.in_(helper_ticket_ids))
        )
    tab_statuses = ["OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "HELP_REQUESTED", "DONE", "CLOSED"]
    status_counts = {s: base_q.filter(Ticket.status == s).count() for s in tab_statuses}

    employees = db.query(User).filter(
        User.tenant_id == tid, User.is_deleted == False,
        User.is_active == True).all()
    _all_depts = db.query(Department).filter(
        Department.tenant_id == tid, Department.is_deleted == False).all()
    # Deduplicate departments by name (same dept can exist per-branch)
    departments = list({d.name: d for d in sorted(_all_depts, key=lambda d: d.name)}.values())
    managers = [e for e in employees if e.role in ("MANAGER", "ADMIN")]
    branches = db.query(Branch).filter(Branch.tenant_id == tid).all()

    statuses = ["OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "HELP_REQUESTED", "DONE", "CLOSED"]

    from .linked_entities import get_linked_entity_options
    entity_options = get_linked_entity_options(db, tid)

    return templates.TemplateResponse(request, "tickets.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "tickets": tickets, "employees": employees,
        "departments": departments, "managers": managers, "branches": branches,
        "status_filter": status, "statuses": statuses, "tab_statuses": tab_statuses,
        "status_counts": status_counts,
        "view": "table",
        "dept_id": dept_id, "manager_id": manager_id, "branch_id": branch_id,
        "priority": priority, "ticket_category": ticket_category,
        "assignee_id": assignee_id, "date_from": date_from, "date_to": date_to,
        "entity_options": entity_options,
        "now": datetime.utcnow(),
    })

@app.post("/tickets/create")
async def create_ticket(
    request: Request,
    title: str = Form(...), description: str = Form(...),
    priority: str = Form("MEDIUM"), assignee_id: str = Form(...),
    due_at: str = Form(...), evidence_required: bool = Form(False),
    ticket_category: str = Form("NORMAL"),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    # P5-02: Employees can only create Help tickets
    if user.role == "EMPLOYEE" and ticket_category != "HELP":
        raise HTTPException(403, "Employees can only create Help tickets")
    if user.role not in ("ADMIN", "MANAGER", "EMPLOYEE"):
        raise HTTPException(403)
    # Employees creating help tickets assign to themselves by default
    if user.role == "EMPLOYEE":
        assignee_id = assignee_id or user.id

    ticket = Ticket(
        tenant_id=user.tenant_id, title=title, description=description,
        priority=priority, created_by_id=user.id,
        current_assignee_id=assignee_id,
        due_at=datetime.fromisoformat(due_at),
        ticket_type="D",
    )
    if hasattr(ticket, "evidence_required"):
        ticket.evidence_required = evidence_required
    if hasattr(ticket, "ticket_category"):
        ticket.ticket_category = ticket_category
    db.add(ticket)
    db.flush()
    tenant = db.query(Tenant).get(user.tenant_id)
    tenant.ticket_seq = (tenant.ticket_seq or 0) + 1
    ticket.display_id = f"T-{tenant.ticket_seq:04d}"
    assignee = db.query(User).get(assignee_id)
    log_event(db, ticket.id, user.id, "CREATED", f"Assigned to {assignee.name if assignee else assignee_id}")
    if assignee:
        notify_ticket_assigned(db, ticket, assignee)
    # P5-10: save linked entities
    form_data = await request.form()
    from .linked_entities import save_linked_entities_from_form
    save_linked_entities_from_form(db, dict(form_data), "TICKET", ticket.id, user.tenant_id, user.id)
    db.commit()
    audience = list(set(_admin_ids(db, user.tenant_id) + _manager_ids_for_ticket(db, user.tenant_id, assignee_id) + [assignee_id]))
    broadcast_sync(user.tenant_id, audience, TICKET_ASSIGNED, {
        "ticket_id": ticket.id, "display_id": ticket.display_id,
        "title": ticket.title, "assignee_id": assignee_id,
    })
    return redirect("/tickets")

@app.post("/tickets/{ticket_id}/move")
def move_ticket(ticket_id: str, new_status: str = Form(...),
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Kanban drag-and-drop status change — Phase 0-F-3."""
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id).first()
    if not ticket:
        raise HTTPException(404)
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(403)
    old_status = ticket.status
    ticket.status = new_status
    if new_status == "ACKNOWLEDGED" and not ticket.acknowledged_at:
        ticket.acknowledged_at = datetime.utcnow()
    if new_status in ("CLOSED", "DONE"):
        ticket.closed_at = datetime.utcnow()
    log_event(db, ticket_id, user.id, "STATUS_CHANGED", f"{old_status} → {new_status}")
    admins   = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for_ticket(db, user.tenant_id, ticket.current_assignee_id)
    notify_ticket_status_changed(db, ticket, user.id, old_status, new_status, admins, managers)
    db.commit()
    audience = list(set(admins + managers + [ticket.current_assignee_id]))
    broadcast_sync(user.tenant_id, audience, TICKET_STATUS_CHANGED, {
        "ticket_id": ticket_id, "display_id": ticket.display_id,
        "old_status": old_status, "new_status": new_status,
    })
    return redirect(f"/tickets?view=kanban")

@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(ticket_id: str, request: Request,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id).first()
    if not ticket:
        raise HTTPException(404)
    employees = db.query(User).filter(
        User.tenant_id == user.tenant_id, User.is_deleted == False).all()
    media = db.query(MediaUpload).filter(
        MediaUpload.entity_type == "ticket",
        MediaUpload.entity_id == ticket_id,
    ).order_by(MediaUpload.created_at).all()
    helper_ids = [h.user_id for h in ticket.helpers]
    from .linked_entities import get_linked_entity_options
    from .database import LinkedEntityReference
    linked_refs = db.query(LinkedEntityReference).filter(
        LinkedEntityReference.tenant_id == user.tenant_id,
        LinkedEntityReference.parent_type == "TICKET",
        LinkedEntityReference.parent_id == ticket_id,
    ).order_by(LinkedEntityReference.created_at).all()
    entity_options = get_linked_entity_options(db, user.tenant_id)
    return templates.TemplateResponse(request, "ticket_detail.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "ticket": ticket, "employees": employees,
        "media": media, "helper_ids": helper_ids,
        "linked_refs": linked_refs, "entity_options": entity_options,
        "now": datetime.utcnow(),
    })

@app.post("/tickets/{ticket_id}/advance")
async def ticket_advance(ticket_id: str, request: Request,
                         user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """P5-03: Mark as Done quick action — advances ticket to next status."""
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id,
        Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(404)
    _status_seq = {"OPEN": "ACKNOWLEDGED", "ACKNOWLEDGED": "IN_PROGRESS",
                   "IN_PROGRESS": "DONE", "DONE": "CLOSED",
                   "HELP_REQUESTED": "IN_PROGRESS"}
    next_status = _status_seq.get(ticket.status)
    if not next_status:
        return redirect(f"/tickets")
    # Evidence gate: IN_PROGRESS → DONE requires upload if evidence_required
    if (next_status == "DONE" and getattr(ticket, "evidence_required", False)):
        form = await request.form()
        file = form.get("evidence_file")
        if file and hasattr(file, "filename") and file.filename:
            from .uploads import save_upload
            info = await save_upload(file, user.tenant_id)
            db.add(MediaUpload(
                tenant_id=user.tenant_id, entity_type="ticket", entity_id=ticket_id,
                uploaded_by_id=user.id, **info,
            ))
            log_event(db, ticket_id, user.id, "EVIDENCE_UPLOADED", info["file_name"])
        else:
            return redirect(f"/tickets?evidence_error={ticket_id}")
    old_status = ticket.status
    ticket.status = next_status
    if next_status == "ACKNOWLEDGED" and not ticket.acknowledged_at:
        ticket.acknowledged_at = datetime.utcnow()
    if next_status in ("DONE", "CLOSED"):
        ticket.closed_at = datetime.utcnow()
    log_event(db, ticket_id, user.id, "STATUS_CHANGED", f"{old_status} → {next_status}")
    admins = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for_ticket(db, user.tenant_id, ticket.current_assignee_id)
    notify_ticket_status_changed(db, ticket, user.id, old_status, next_status, admins, managers)
    db.commit()
    audience = list(set(admins + managers + [ticket.current_assignee_id]))
    broadcast_sync(user.tenant_id, audience, TICKET_STATUS_CHANGED, {
        "ticket_id": ticket_id, "display_id": ticket.display_id,
        "old_status": old_status, "new_status": next_status,
    })
    return redirect(f"/tickets?status={old_status}&advanced=1")


@app.post("/tickets/{ticket_id}/revert")
async def ticket_revert(ticket_id: str, user: User = Depends(require_manager),
                        db: Session = Depends(get_db)):
    """Revert ticket one stage back. Admin/Manager only."""
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id,
        Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(404)
    _prev_seq = {
        "ACKNOWLEDGED":   "OPEN",
        "IN_PROGRESS":    "ACKNOWLEDGED",
        "HELP_REQUESTED": "IN_PROGRESS",
        "DONE":           "IN_PROGRESS",
        "CLOSED":         "DONE",
    }
    prev_status = _prev_seq.get(ticket.status)
    if not prev_status:
        return redirect("/tickets")
    old_status = ticket.status
    ticket.status = prev_status
    db.commit()
    return redirect(f"/tickets?status={old_status}&advanced=1")


@app.post("/tickets/bulk-action")
async def tickets_bulk_action(
    request: Request,
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    """Bulk advance / close / revert selected tickets."""
    form = await request.form()
    action = form.get("action", "")
    ids = form.getlist("ticket_ids")
    if not ids or action not in ("advance", "close", "revert"):
        return redirect("/tickets")

    _next = {"OPEN": "ACKNOWLEDGED", "ACKNOWLEDGED": "IN_PROGRESS",
             "IN_PROGRESS": "DONE", "DONE": "CLOSED", "HELP_REQUESTED": "IN_PROGRESS"}
    _prev = {"ACKNOWLEDGED": "OPEN", "IN_PROGRESS": "ACKNOWLEDGED",
             "HELP_REQUESTED": "IN_PROGRESS", "DONE": "IN_PROGRESS", "CLOSED": "DONE"}

    tickets = db.query(Ticket).filter(
        Ticket.id.in_(ids), Ticket.tenant_id == user.tenant_id,
        Ticket.is_deleted == False).all()

    for t in tickets:
        if action == "advance":
            ns = _next.get(t.status)
            if ns:
                t.status = ns
        elif action == "close":
            if t.status not in ("CLOSED",):
                t.status = "CLOSED"
        elif action == "revert":
            ps = _prev.get(t.status)
            if ps:
                t.status = ps
    db.commit()
    return redirect("/tickets?advanced=1")


@app.post("/tickets/{ticket_id}/remind")
def ticket_remind(ticket_id: str, user: User = Depends(require_manager),
                  db: Session = Depends(get_db)):
    """P5-06: Send Reminder — Admin/Manager only."""
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id,
        Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(404)
    assignee = db.query(User).get(ticket.current_assignee_id)
    if assignee:
        notify_ticket_reminder(db, ticket, assignee)
    log_event(db, ticket_id, user.id, "REMINDER_SENT", f"Manual reminder to {assignee.name if assignee else '?'}")
    db.commit()
    return redirect(f"/tickets/{ticket_id}?reminded=1")


@app.post("/tickets/{ticket_id}/edit")
def ticket_edit(ticket_id: str, title: str = Form(...), description: str = Form(...),
                priority: str = Form("MEDIUM"), assignee_id: str = Form(...),
                due_at: str = Form(...), evidence_required: bool = Form(False),
                ticket_category: str = Form("NORMAL"),
                user: User = Depends(require_manager), db: Session = Depends(get_db)):
    """P5-08: Edit ticket — Admin/Manager only."""
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id,
        Ticket.is_deleted == False, Ticket.status != "CLOSED").first()
    if not ticket:
        raise HTTPException(404)
    old = f"{ticket.title} | {ticket.priority} | {ticket.current_assignee_id}"
    old_assignee_id = ticket.current_assignee_id
    ticket.title = title
    ticket.description = description
    ticket.priority = priority
    ticket.current_assignee_id = assignee_id
    try:
        ticket.due_at = datetime.fromisoformat(due_at)
    except Exception:
        pass
    if hasattr(ticket, "evidence_required"):
        ticket.evidence_required = evidence_required
    if hasattr(ticket, "ticket_category"):
        ticket.ticket_category = ticket_category
    log_event(db, ticket_id, user.id, "EDITED", f"Previous: {old}")
    db.commit()
    if assignee_id != old_assignee_id:
        new_assignee = db.query(User).filter(User.id == assignee_id).first()
        if new_assignee:
            notify_ticket_assigned(db, ticket, new_assignee)
    return redirect(f"/tickets/{ticket_id}")


@app.post("/tickets/{ticket_id}/delete")
def ticket_delete(ticket_id: str, user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """P5-09: Soft delete — Admin only."""
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id).first()
    if not ticket:
        raise HTTPException(404)
    ticket.is_deleted = True
    log_event(db, ticket_id, user.id, "DELETED", "Soft deleted by admin")
    db.commit()
    return redirect("/tickets")


@app.get("/tickets/bulk-template")
def tickets_bulk_template(user: User = Depends(require_manager)):
    """P5-07: CSV template download."""
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["title","description","priority","ticket_category","assignee_phone","due_at","evidence_required"])
    w.writerow(["Mandatory. Short title, max 200 chars.","Mandatory. Full task description.","LOW / MEDIUM / HIGH / CRITICAL","NORMAL or HELP (default NORMAL)","Mandatory. 10-digit phone of assignee.","Mandatory. YYYY-MM-DD HH:MM","TRUE or FALSE (default FALSE)"])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=tickets_template.csv"})


@app.post("/tickets/bulk-upload")
async def tickets_bulk_upload(file: UploadFile = File(...),
                               user: User = Depends(require_manager),
                               db: Session = Depends(get_db)):
    """P5-07: Bulk upload tickets from CSV."""
    tid = user.tenant_id
    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    errors = []
    created = 0
    tenant = db.query(Tenant).get(tid)
    for i, row in enumerate(reader, start=2):
        title = (row.get("title") or "").strip()
        # Skip description/header-like rows
        if title.lower().startswith("mandatory") or title.lower().startswith("optional"):
            continue
        description = (row.get("description") or "").strip()
        priority = (row.get("priority") or "MEDIUM").strip().upper()
        category = (row.get("ticket_category") or "NORMAL").strip().upper()
        phone = (row.get("assignee_phone") or "").strip()
        due_str = (row.get("due_at") or "").strip()
        ev_req = (row.get("evidence_required") or "FALSE").strip().upper() == "TRUE"

        if not title:
            errors.append(f"Row {i}: title is required"); continue
        if not description:
            errors.append(f"Row {i}: description is required"); continue
        if priority not in ("LOW","MEDIUM","HIGH","CRITICAL"):
            errors.append(f"Row {i}: invalid priority '{priority}'"); continue
        if category not in ("NORMAL","HELP"):
            errors.append(f"Row {i}: invalid ticket_category '{category}'"); continue
        if not phone:
            errors.append(f"Row {i}: assignee_phone is required"); continue
        assignee = db.query(User).filter(User.phone == phone, User.tenant_id == tid,
                                          User.is_active == True, User.is_deleted == False).first()
        if not assignee:
            errors.append(f"Row {i}: no active user with phone '{phone}'"); continue
        if not due_str:
            errors.append(f"Row {i}: due_at is required"); continue
        try:
            due_dt = datetime.strptime(due_str, "%Y-%m-%d %H:%M")
        except Exception:
            errors.append(f"Row {i}: due_at must be YYYY-MM-DD HH:MM, got '{due_str}'"); continue

        ticket = Ticket(
            tenant_id=tid, title=title[:200], description=description,
            priority=priority, created_by_id=user.id,
            current_assignee_id=assignee.id, due_at=due_dt, ticket_type="D",
        )
        if hasattr(ticket, "evidence_required"):
            ticket.evidence_required = ev_req
        if hasattr(ticket, "ticket_category"):
            ticket.ticket_category = category
        db.add(ticket)
        db.flush()
        tenant.ticket_seq = (tenant.ticket_seq or 0) + 1
        ticket.display_id = f"T-{tenant.ticket_seq:04d}"
        log_event(db, ticket.id, user.id, "CREATED", f"Bulk upload — assigned to {assignee.name}")
        notify_ticket_assigned(db, ticket, assignee)
        created += 1

    db.commit()
    if errors:
        import io as _io
        buf = _io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Row","Error"])
        for e in errors:
            parts = e.split(": ", 1)
            w.writerow(parts if len(parts)==2 else [e, ""])
        buf.seek(0)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=ticket_upload_errors.csv"})
    return redirect(f"/tickets?bulk_created={created}")


@app.post("/tickets/{ticket_id}/action")
def ticket_action(ticket_id: str, action: str = Form(...),
                  comment: str = Form(""), new_assignee_id: str = Form(""),
                  flag_reason: str = Form(""), what_completed: str = Form(""),
                  why_reassigning: str = Form(""),
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id).first()
    if not ticket:
        raise HTTPException(404)

    admins   = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for_ticket(db, user.tenant_id, ticket.current_assignee_id)

    if action == "acknowledge":
        old_status = ticket.status
        ticket.status = "ACKNOWLEDGED"
        ticket.acknowledged_at = datetime.utcnow()
        log_event(db, ticket_id, user.id, "ACKNOWLEDGED")
        notify_ticket_status_changed(db, ticket, user.id, old_status, "ACKNOWLEDGED", admins, managers)
    elif action == "start":
        old_status = ticket.status
        ticket.status = "IN_PROGRESS"
        log_event(db, ticket_id, user.id, "STARTED")
        notify_ticket_status_changed(db, ticket, user.id, old_status, "IN_PROGRESS", admins, managers)
    elif action == "done":
        old_status = ticket.status
        ticket.status = "DONE"
        log_event(db, ticket_id, user.id, "DONE")
        notify_ticket_status_changed(db, ticket, user.id, old_status, "DONE", admins, managers)
    elif action == "close":
        old_status = ticket.status
        ticket.status = "CLOSED"
        ticket.closed_at = datetime.utcnow()
        log_event(db, ticket_id, user.id, "CLOSED")
        notify_ticket_status_changed(db, ticket, user.id, old_status, "CLOSED", admins, managers)
    elif action == "comment" and comment.strip():
        db.add(TicketComment(ticket_id=ticket_id, user_id=user.id, body=comment.strip()))
        helper_ids = [h.user_id for h in ticket.helpers]
        notify_ticket_commented(db, ticket, user.id, helper_ids)
    elif action == "reassign" and new_assignee_id and what_completed and why_reassigning:
        helper_ids_chk = [h.user_id for h in ticket.helpers]
        can_reassign = (
            user.id == ticket.current_assignee_id
            or user.id in helper_ids_chk
            or user.role in ("ADMIN", "MANAGER")
        )
        if not can_reassign:
            raise HTTPException(status_code=403, detail="Not authorized to reassign this ticket")
        ticket.current_assignee_id = new_assignee_id
        ticket.status = "OPEN"
        log_event(db, ticket_id, user.id, "REASSIGNED",
                  f"Completed: {what_completed} | Reason: {why_reassigning} | To: {new_assignee_id}")
        new_assignee = db.query(User).get(new_assignee_id)
        if new_assignee:
            notify_ticket_assigned(db, ticket, new_assignee)
    elif action == "flag" and flag_reason:
        if user.role == "EMPLOYEE":
            if ticket.current_assignee_id != user.id:
                raise HTTPException(status_code=403, detail="Only the current assignee can escalate")
            recent_escalation = db.query(TicketEvent).filter(
                TicketEvent.ticket_id == ticket_id,
                TicketEvent.actor_id == user.id,
                TicketEvent.event_type == "FLAGGED",
                TicketEvent.created_at > (datetime.utcnow() - timedelta(hours=24)),
            ).first()
            if recent_escalation:
                return redirect(f"/tickets/{ticket_id}?escalation_error=1")
        ticket.is_flagged = True
        ticket.flagged_reason = flag_reason
        log_event(db, ticket_id, user.id, "FLAGGED", flag_reason)
        notify_ticket_flagged(db, ticket, user.id, admins,
                              manager_ids=managers, actor_name=user.name)
    elif action == "unflag":
        ticket.is_flagged = False
        ticket.flagged_reason = None
        log_event(db, ticket_id, user.id, "UNFLAGGED")
    elif action == "help_request" and comment.strip():
        ticket.status = "HELP_REQUESTED"
        log_event(db, ticket_id, user.id, "HELP_REQUESTED", comment.strip())
        notify_ticket_help_requested(db, ticket, user.id, admins, managers)
    elif action == "reopen":
        ticket.status = "OPEN"
        ticket.closed_at = None
        log_event(db, ticket_id, user.id, "REOPENED")

    db.commit()

    # Real-time sync — broadcast to relevant users for status-changing actions
    if action == "help_request":
        audience = list(set(admins + managers + [ticket.current_assignee_id]))
        broadcast_sync(user.tenant_id, audience, TICKET_HELP_REQUESTED, {
            "ticket_id": ticket_id, "display_id": ticket.display_id,
            "status": ticket.status, "requester": user.name,
        })
    elif action in ("acknowledge", "start", "done", "close", "reopen"):
        audience = list(set(admins + managers + [ticket.current_assignee_id]))
        broadcast_sync(user.tenant_id, audience, TICKET_STATUS_CHANGED, {
            "ticket_id": ticket_id, "display_id": ticket.display_id,
            "status": ticket.status, "action": action,
        })
    elif action == "comment" and comment.strip():
        helper_ids_ws = [h.user_id for h in ticket.helpers]
        audience = list(set(admins + [ticket.created_by_id, ticket.current_assignee_id] + helper_ids_ws))
        broadcast_sync(user.tenant_id, audience, TICKET_COMMENTED, {
            "ticket_id": ticket_id, "display_id": ticket.display_id,
            "commenter": user.name,
        })
    elif action in ("flag", "unflag"):
        audience = list(set(admins + [ticket.current_assignee_id]))
        broadcast_sync(user.tenant_id, audience, TICKET_FLAGGED, {
            "ticket_id": ticket_id, "display_id": ticket.display_id, "flagged": ticket.is_flagged,
        })

    return redirect(f"/tickets/{ticket_id}")

@app.post("/tickets/{ticket_id}/add-helper")
def add_helper(ticket_id: str, helper_id: str = Form(...), note: str = Form(""),
               user: User = Depends(require_manager), db: Session = Depends(get_db)):
    """Phase 0-C-1/2: add a helper to a ticket."""
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id).first()
    if not ticket:
        raise HTTPException(404)
    # Avoid duplicates
    existing = db.query(TicketAssignee).filter(
        TicketAssignee.ticket_id == ticket_id,
        TicketAssignee.user_id == helper_id,
    ).first()
    if not existing:
        db.add(TicketAssignee(ticket_id=ticket_id, user_id=helper_id,
                              added_by_id=user.id, note=note.strip()))
        helper = db.query(User).get(helper_id)
        log_event(db, ticket_id, user.id, "HELPER_ADDED", f"Helper: {helper.name if helper else helper_id}")
        if helper:
            notify_helper_added(db, ticket, helper)
    db.commit()
    # Notify the new helper and the ticket owner in real-time
    audience = list(set([helper_id, ticket.current_assignee_id] + admins))
    broadcast_sync(user.tenant_id, audience, TICKET_ASSIGNED, {
        "ticket_id": ticket_id, "display_id": ticket.display_id,
        "action": "helper_added",
    })
    return redirect(f"/tickets/{ticket_id}")

@app.post("/tickets/{ticket_id}/remove-helper")
def remove_helper(ticket_id: str, helper_id: str = Form(...),
                  user: User = Depends(require_manager), db: Session = Depends(get_db)):
    db.query(TicketAssignee).filter(
        TicketAssignee.ticket_id == ticket_id,
        TicketAssignee.user_id == helper_id,
    ).delete()
    removed = db.query(User).get(helper_id)
    log_event(db, ticket_id, user.id, "HELPER_REMOVED", f"Helper: {removed.name if removed else helper_id}")
    db.commit()
    return redirect(f"/tickets/{ticket_id}")

@app.post("/tickets/{ticket_id}/upload")
async def upload_ticket_media(ticket_id: str, file: UploadFile = File(...),
                               user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    """Phase 0-C-3 / 0-E-1: upload proof photo to a ticket."""
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id).first()
    if not ticket:
        raise HTTPException(404)
    info = await save_upload(file, user.tenant_id)
    db.add(MediaUpload(
        tenant_id=user.tenant_id, entity_type="ticket", entity_id=ticket_id,
        uploaded_by_id=user.id, **info,
    ))
    log_event(db, ticket_id, user.id, "PROOF_UPLOADED", info["file_name"])
    db.commit()
    return redirect(f"/tickets/{ticket_id}")


# ── Checklists ────────────────────────────────────────────────────────────────

def _next_due_from(freq: str, from_dt: datetime) -> datetime:
    """Compute next due datetime based on frequency. Always returns a future datetime."""
    _delta = {
        "DAILY":        timedelta(days=1),
        "WEEKLY":       timedelta(weeks=1),
        "TWICE_A_MONTH": timedelta(days=15),
        "MONTHLY":      timedelta(days=30),
        "QUARTERLY":    timedelta(days=91),
        "YEARLY":       timedelta(days=365),
        "PER_SHIFT":    timedelta(hours=8),
    }.get(freq, timedelta(days=1))
    nxt = from_dt + _delta
    _now = datetime.utcnow()
    # If the computed next date is already in the past, keep advancing until future
    while nxt < _now:
        nxt += _delta
    return nxt


def _checklist_stats(db: Session, tmpl) -> dict:
    """Compute live stats for one ChecklistTemplate."""
    all_a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == tmpl.id,
        ChecklistAssignment.is_deleted == False,
    ).all()
    total = len(all_a)
    done = sum(1 for a in all_a if a.status == "DONE")
    failed = sum(1 for a in all_a if a.status == "FAILED")
    compliance = round(done / total * 100) if total else 0
    last_done = max(
        (a.completed_at for a in all_a if a.status == "DONE" and a.completed_at),
        default=None)
    next_pending = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == tmpl.id,
        ChecklistAssignment.is_deleted == False,
        ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
    ).order_by(ChecklistAssignment.due_at).first()
    return {
        "total": total, "done": done, "failed": failed,
        "compliance": compliance,
        "last_completed": last_done,
        "next_due": next_pending.due_at if next_pending else None,
        "next_assignment": next_pending,
    }


@app.get("/checklists", response_class=HTMLResponse)
def checklists(request: Request, user: User = Depends(get_current_user),
               db: Session = Depends(get_db),
               dept_id: List[str] = Query([]), manager_id: List[str] = Query([]),
               employee_id: List[str] = Query([]), branch_id: List[str] = Query([]),
               next_days: int = 7):
    tid = user.tenant_id
    now = datetime.utcnow()

    # My overdue: date already gone, not done
    my_overdue = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == tid,
        ChecklistAssignment.user_id == user.id,
        ChecklistAssignment.due_at < now,
        ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
        ChecklistAssignment.is_deleted == False,
    ).order_by(ChecklistAssignment.due_at).all()

    # My upcoming: due in the future, not done
    my_upcoming = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == tid,
        ChecklistAssignment.user_id == user.id,
        ChecklistAssignment.due_at >= now,
        ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
        ChecklistAssignment.is_deleted == False,
    ).order_by(ChecklistAssignment.due_at).all()

    # For backwards-compat: my_assignments = overdue + upcoming (for my-section rendering)
    my_assignments = my_overdue + my_upcoming

    # ── Auto-repair: silently schedule missing next-occurrences for recurring checklists ──
    if user.role in ("ADMIN", "MANAGER"):
        _all_t = db.query(ChecklistTemplate).filter(
            ChecklistTemplate.tenant_id == tid,
            ChecklistTemplate.is_deleted == False,
            ChecklistTemplate.is_active == True,
        ).all()
        _repaired = False
        for _t in _all_t:
            if not (getattr(_t, 'is_recurring', True) and _t.assigned_to_user_id):
                continue
            _has = db.query(ChecklistAssignment).filter(
                ChecklistAssignment.template_id == _t.id,
                ChecklistAssignment.user_id == _t.assigned_to_user_id,
                ChecklistAssignment.is_deleted == False,
                ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
            ).first()
            if _has:
                continue
            _last = db.query(ChecklistAssignment).filter(
                ChecklistAssignment.template_id == _t.id,
                ChecklistAssignment.user_id == _t.assigned_to_user_id,
                ChecklistAssignment.status == "DONE",
            ).order_by(ChecklistAssignment.due_at.desc()).first()
            _base = (_last.due_at if (_last and _last.due_at) else now)
            _nxt = _next_due_from(_t.frequency, _base)
            db.add(ChecklistAssignment(
                template_id=_t.id, tenant_id=tid,
                user_id=_t.assigned_to_user_id, due_at=_nxt,
                evidence_required=bool(_t.evidence_required),
            ))
            _repaired = True
        if _repaired:
            db.commit()

    # Upcoming + overdue assignments for admin/manager across team
    next_days = max(1, min(next_days, 90))
    upcoming = []
    overdue_team = []
    failed_team = []
    cl_team_ids = []
    if user.role in ("ADMIN", "MANAGER"):
        if user.role == "MANAGER":
            cl_team_ids = [u.id for u in db.query(User).filter(
                User.manager_id == user.id, User.is_deleted == False).all()]
            cl_team_ids.append(user.id)
        # Only show assignments whose template still exists and is active (not deleted)
        _active_tmpl_ids = db.query(ChecklistTemplate.id).filter(
            ChecklistTemplate.tenant_id == tid,
            ChecklistTemplate.is_deleted == False,
            ChecklistTemplate.is_active == True,
        ).scalar_subquery()
        upcoming_q = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.due_at >= now,
            ChecklistAssignment.due_at <= now + timedelta(days=next_days),
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ChecklistAssignment.is_deleted == False,
            ChecklistAssignment.template_id.in_(_active_tmpl_ids),
        )
        overdue_q = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.due_at < now,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
            ChecklistAssignment.is_deleted == False,
            ChecklistAssignment.template_id.in_(_active_tmpl_ids),
        )
        if cl_team_ids:
            upcoming_q = upcoming_q.filter(ChecklistAssignment.user_id.in_(cl_team_ids))
            overdue_q = overdue_q.filter(ChecklistAssignment.user_id.in_(cl_team_ids))
        if employee_id:
            upcoming_q = upcoming_q.filter(ChecklistAssignment.user_id.in_(employee_id))
            overdue_q = overdue_q.filter(ChecklistAssignment.user_id.in_(employee_id))
        # Deduplicate upcoming: one row per template — earliest due_at wins
        _all_upcoming = upcoming_q.order_by(ChecklistAssignment.due_at).all()
        _seen_tmpl = {}
        for _a in _all_upcoming:
            if _a.template_id not in _seen_tmpl:
                _seen_tmpl[_a.template_id] = _a
        upcoming = list(_seen_tmpl.values())

        # Deduplicate overdue: one row per template — earliest due_at wins
        _all_overdue = overdue_q.order_by(ChecklistAssignment.due_at).all()
        _seen_od = {}
        for _a in _all_overdue:
            if _a.template_id not in _seen_od:
                _seen_od[_a.template_id] = _a
        overdue_team = list(_seen_od.values())

        # Failed assignments (explicitly marked FAILED, last 90 days)
        failed_q = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.status == "FAILED",
            ChecklistAssignment.is_deleted == False,
            ChecklistAssignment.template_id.in_(_active_tmpl_ids),
        )
        if cl_team_ids:
            failed_q = failed_q.filter(ChecklistAssignment.user_id.in_(cl_team_ids))
        if employee_id:
            failed_q = failed_q.filter(ChecklistAssignment.user_id.in_(employee_id))
        failed_assignments = failed_q.order_by(ChecklistAssignment.due_at.desc()).limit(50).all()

        # Missed assignments: PENDING/OVERDUE past due_at and superseded by a newer occurrence
        old_overdue_q = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
            ChecklistAssignment.is_deleted == False,
            ChecklistAssignment.due_at < now,
            ChecklistAssignment.template_id.in_(_active_tmpl_ids),
        )
        if cl_team_ids:
            old_overdue_q = old_overdue_q.filter(ChecklistAssignment.user_id.in_(cl_team_ids))
        if employee_id:
            old_overdue_q = old_overdue_q.filter(ChecklistAssignment.user_id.in_(employee_id))
        old_overdue_candidates = old_overdue_q.all()

        # Keep only those that have a newer sibling (= they were skipped / superseded)
        missed_assignments = []
        for _a in old_overdue_candidates:
            _newer = db.query(ChecklistAssignment.id).filter(
                ChecklistAssignment.template_id == _a.template_id,
                ChecklistAssignment.user_id == _a.user_id,
                ChecklistAssignment.due_at > _a.due_at,
                ChecklistAssignment.is_deleted == False,
            ).first()
            if _newer:
                missed_assignments.append(_a)

        # Merge and deduplicate by id, sort by due_at desc
        _seen = set()
        failed_team = []
        for _a in sorted(failed_assignments + missed_assignments, key=lambda x: x.due_at or now, reverse=True):
            if _a.id not in _seen:
                _seen.add(_a.id)
                failed_team.append(_a)

    templates_list = []
    if user.role in ("ADMIN", "MANAGER"):
        q = db.query(ChecklistTemplate).filter(
            ChecklistTemplate.tenant_id == tid,
            ChecklistTemplate.is_deleted == False,
        )
        if dept_id:
            q = q.filter(ChecklistTemplate.assigned_to_dept_id.in_(dept_id))
        if manager_id:
            sub_user_ids = []
            for mid in manager_id:
                sub_user_ids += [u.id for u in db.query(User).filter(
                    User.tenant_id == tid, User.manager_id == mid,
                    User.is_deleted == False).all()]
            if sub_user_ids:
                q = q.filter(ChecklistTemplate.assigned_to_user_id.in_(sub_user_ids))
        if employee_id:
            q = q.filter(ChecklistTemplate.assigned_to_user_id.in_(employee_id))
        if branch_id:
            branch_user_ids = [u.id for u in db.query(User).filter(
                User.tenant_id == tid, User.branch_id.in_(branch_id),
                User.is_deleted == False).all()]
            if branch_user_ids:
                q = q.filter(ChecklistTemplate.assigned_to_user_id.in_(branch_user_ids))
        templates_list = q.order_by(ChecklistTemplate.created_at.desc()).all()
        for tmpl in templates_list:
            tmpl._stats = _checklist_stats(db, tmpl)

    # Weekly completion chart — last 8 weeks
    chart_weeks = []
    for i in range(7, -1, -1):
        week_start = (now - timedelta(weeks=i)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        week_start -= timedelta(days=week_start.weekday())
        week_end = week_start + timedelta(days=7)
        done_count = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.status == "DONE",
            ChecklistAssignment.completed_at >= week_start,
            ChecklistAssignment.completed_at < week_end,
        ).count()
        fail_count = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.status == "FAILED",
            ChecklistAssignment.completed_at >= week_start,
            ChecklistAssignment.completed_at < week_end,
        ).count()
        chart_weeks.append({
            "label": week_start.strftime("W%W"),
            "done": done_count,
            "failed": fail_count,
        })

    _raw_depts = db.query(Department).filter(
        Department.tenant_id == tid, Department.is_deleted == False).all()
    departments = list({d.name: d for d in sorted(_raw_depts, key=lambda d: d.name)}.values())
    employees = db.query(User).filter(
        User.tenant_id == tid, User.is_deleted == False, User.is_active == True,
    ).order_by(User.name).all()
    managers = [e for e in employees if e.role in ("MANAGER", "ADMIN")]
    branches = db.query(Branch).filter(
        Branch.tenant_id == tid, Branch.is_deleted == False,
    ).order_by(Branch.name).all()

    from .linked_entities import get_linked_entity_options as _geo
    entity_options = _geo(db, user.tenant_id)
    return templates.TemplateResponse(request, "checklists.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "my_assignments": my_assignments,
        "my_overdue": my_overdue,
        "my_upcoming": my_upcoming,
        "upcoming": upcoming,
        "overdue_team": overdue_team,
        "failed_team": failed_team,
        "templates_list": templates_list,
        "departments": departments, "employees": employees, "managers": managers,
        "branches": branches,
        "chart_weeks": chart_weeks,
        "dept_id": dept_id, "manager_id": manager_id,
        "employee_id": employee_id, "branch_id": branch_id,
        "next_days": next_days,
        "entity_options": entity_options,
        "now": now,
        "checklist_notif_hours": getattr(user.tenant, "checklist_notif_hours", None) or "8,13,18",
        "checklist_overdue_hour": getattr(user.tenant, "checklist_overdue_hour", None) or "",
    })

@app.post("/checklists/templates/create")
async def create_template(
    request: Request,
    title: str = Form(...), description: str = Form(...),
    frequency: str = Form("DAILY"),
    proof_required: bool = Form(False),
    evidence_required: bool = Form(False),
    assigned_to_user_id: str = Form(""),
    assigned_to_dept_id: str = Form(""),
    assigned_to_role: str = Form("EMPLOYEE"),
    reminder_hours_before: int = Form(2),
    reminder_repeat_hours: int = Form(4),
    is_recurring: bool = Form(True),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    role = assigned_to_role
    if assigned_to_user_id:
        emp = db.query(User).filter(User.id == assigned_to_user_id).first()
        if emp:
            role = emp.role
    tmpl = ChecklistTemplate(
        tenant_id=user.tenant_id, title=title, description=description,
        frequency=frequency, proof_required=proof_required,
        evidence_required=evidence_required,
        assigned_to_role=role,
        assigned_to_dept_id=assigned_to_dept_id or None,
        assigned_to_user_id=assigned_to_user_id or None,
        reminder_hours_before=reminder_hours_before,
        reminder_repeat_hours=reminder_repeat_hours,
        is_recurring=is_recurring,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    # P6-05: save linked entities
    from .linked_entities import save_linked_entities_from_form as _slf
    form_data = dict(await request.form())
    _slf(db, form_data, "CHECKLIST_TEMPLATE", tmpl.id, user.tenant_id, user.id)
    return redirect("/checklists")

@app.post("/checklists/assign/{template_id}")
def assign_checklist(template_id: str, due_at: str = Form(...),
                     user: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    tmpl = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(404)

    # Phase 0-B-8/9: respect dept/user-level assignment
    if tmpl.assigned_to_user_id:
        target_users = db.query(User).filter(
            User.id == tmpl.assigned_to_user_id, User.is_active == True,
            User.is_deleted == False, User.tenant_id == user.tenant_id).all()
    elif tmpl.assigned_to_dept_id:
        target_users = db.query(User).filter(
            User.department_id == tmpl.assigned_to_dept_id,
            User.tenant_id == user.tenant_id,
            User.is_active == True, User.is_deleted == False).all()
    else:
        target_users = db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.role == tmpl.assigned_to_role,
            User.is_active == True, User.is_deleted == False).all()

    due = datetime.fromisoformat(due_at)
    for u in target_users:
        db.add(ChecklistAssignment(
            template_id=template_id, tenant_id=user.tenant_id,
            user_id=u.id, due_at=due,
        ))
    db.commit()
    return redirect("/checklists")


@app.post("/checklists/bulk-start")
async def checklists_bulk_start(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk mark selected checklist assignments as IN_PROGRESS."""
    form = await request.form()
    ids = form.getlist("assignment_ids")
    if ids:
        assignments = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.id.in_(ids),
            ChecklistAssignment.user_id == user.id,
            ChecklistAssignment.status == "PENDING",
        ).all()
        for a in assignments:
            a.status = "IN_PROGRESS"
        db.commit()
    return redirect("/checklists")


@app.post("/checklists/bulk-complete")
async def checklists_bulk_complete(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk mark selected checklist assignments as DONE (no evidence gate)."""
    form = await request.form()
    ids = form.getlist("assignment_ids")
    if ids:
        assignments = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.id.in_(ids),
            ChecklistAssignment.user_id == user.id,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
        ).all()
        for a in assignments:
            a.status = "DONE"
            a.completed_at = datetime.utcnow()
            # auto-schedule next occurrence
            tmpl = a.template
            if tmpl and getattr(tmpl, "is_recurring", True):
                next_due = _next_due_from(tmpl.frequency, a.due_at or datetime.utcnow())
                existing = db.query(ChecklistAssignment).filter(
                    ChecklistAssignment.template_id == tmpl.id,
                    ChecklistAssignment.user_id == a.user_id,
                    ChecklistAssignment.due_at == next_due,
                    ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
                ).first()
                if not existing:
                    db.add(ChecklistAssignment(
                        template_id=tmpl.id, tenant_id=a.tenant_id,
                        user_id=a.user_id, due_at=next_due,
                        evidence_required=bool(tmpl.evidence_required),
                    ))
        db.commit()
    return redirect("/checklists")


@app.post("/checklists/start/{assignment_id}")
def start_checklist(assignment_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """P6-01: PENDING → IN_PROGRESS."""
    q = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.is_deleted == False,
    )
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(ChecklistAssignment.user_id == user.id)
    else:
        q = q.filter(ChecklistAssignment.tenant_id == user.tenant_id)
    a = q.first()
    if not a:
        raise HTTPException(404)
    if a.status == "PENDING":
        a.status = "IN_PROGRESS"
        db.commit()
    return redirect("/checklists")


@app.post("/checklists/complete/{assignment_id}")
async def complete_checklist(assignment_id: str, request: Request,
                              delay_reason: str = Form(""),
                              evidence_file: UploadFile = File(None),
                              user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)):
    """P6-01/P6-06: Mark assignment complete; gate evidence upload and delay reason."""
    q = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.is_deleted == False,
    )
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(ChecklistAssignment.user_id == user.id)
    else:
        q = q.filter(ChecklistAssignment.tenant_id == user.tenant_id)
    a = q.first()
    if not a:
        raise HTTPException(404)
    # P6-01: delay_reason required for OVERDUE
    if a.status == "OVERDUE" and not delay_reason.strip():
        return redirect("/checklists?err=Delay+reason+is+required+for+overdue+assignments")
    # P6-06: evidence required gate
    ev_required = bool(a.evidence_required or (a.template and a.template.evidence_required))
    if ev_required and (not evidence_file or not evidence_file.filename):
        return redirect("/checklists?err=Evidence+file+is+required+for+this+checklist+%E2%80%94+please+use+the+Complete+button+which+opens+the+upload+form")
    a.status = "DONE"
    a.completed_at = datetime.utcnow()
    if delay_reason.strip():
        a.delay_reason = delay_reason.strip()
    # Save evidence file
    if evidence_file and evidence_file.filename:
        info = await save_upload(evidence_file, user.tenant_id)
        db.add(MediaUpload(
            tenant_id=user.tenant_id, entity_type="CHECKLIST_ASSIGNMENT",
            entity_id=assignment_id, uploaded_by_id=user.id, **info,
        ))
        a.proof_url = info["file_path"]
    admins   = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for_ticket(db, user.tenant_id, user.id)
    notify_checklist_completed(db, a, admins, managers)
    tmpl = a.template
    if tmpl and getattr(tmpl, "is_recurring", True):
        next_due = _next_due_from(tmpl.frequency, a.due_at)
        existing = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == tmpl.id,
            ChecklistAssignment.user_id == a.user_id,
            ChecklistAssignment.due_at == next_due,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
        ).first()
        if not existing:
            db.add(ChecklistAssignment(
                template_id=tmpl.id, tenant_id=a.tenant_id,
                user_id=a.user_id, due_at=next_due,
                evidence_required=ev_required,
            ))
    db.commit()
    audience = list(set(admins + managers))
    broadcast_sync(user.tenant_id, audience, CHECKLIST_COMPLETED, {
        "checklist": tmpl.title if tmpl else "",
        "completed_by": user.name,
    })
    return redirect("/checklists")


@app.post("/checklists/fail/{assignment_id}")
def fail_checklist(assignment_id: str,
                   failure_note: str = Form(""),
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.user_id == user.id,
    ).first()
    if not a:
        raise HTTPException(404)
    a.status = "FAILED"
    a.completed_at = datetime.utcnow()
    a.failure_note = failure_note or None
    # Still auto-schedule next for recurring
    tmpl = a.template
    if tmpl and getattr(tmpl, "is_recurring", True):
        next_due = _next_due_from(tmpl.frequency, a.due_at)
        existing = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == tmpl.id,
            ChecklistAssignment.user_id == a.user_id,
            ChecklistAssignment.due_at == next_due,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
        ).first()
        if not existing:
            db.add(ChecklistAssignment(
                template_id=tmpl.id, tenant_id=a.tenant_id,
                user_id=a.user_id, due_at=next_due,
            ))
    db.commit()
    return redirect("/checklists")


@app.get("/checklists/history/{template_id}", response_class=HTMLResponse)
def checklist_history(request: Request, template_id: str,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    tmpl = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.id == template_id,
        ChecklistTemplate.tenant_id == user.tenant_id,
    ).first()
    if not tmpl:
        raise HTTPException(404)
    history = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == template_id,
    ).order_by(ChecklistAssignment.due_at.desc()).all()
    from markupsafe import Markup as _Markup
    import json as _json
    hist_json = _Markup(_json.dumps([{
        "user": a.user.name if a.user else "—",
        "due": a.due_at.strftime("%d %b %Y, %I:%M %p") if a.due_at else "—",
        "completed": a.completed_at.strftime("%d %b %Y, %I:%M %p") if a.completed_at else None,
        "status": a.status,
        "note": a.failure_note or "",
    } for a in history]))
    return templates.TemplateResponse(request, "checklist_history.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "tmpl": tmpl, "history": history, "hist_json": hist_json,
        "now": datetime.utcnow(),
    })

@app.post("/checklists/comment/{assignment_id}")
def checklist_comment(assignment_id: str, body: str = Form(...),
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Phase 0-B-6: add a comment to a checklist assignment."""
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.tenant_id == user.tenant_id,
    ).first()
    if not a:
        raise HTTPException(404)
    db.add(ChecklistComment(assignment_id=assignment_id,
                             user_id=user.id, body=body.strip()))
    db.commit()
    return redirect("/checklists")

@app.post("/checklists/upload/{assignment_id}")
async def upload_checklist_proof(assignment_id: str, file: UploadFile = File(...),
                                  user: User = Depends(get_current_user),
                                  db: Session = Depends(get_db)):
    """Phase 0-B-7: upload proof photo for a checklist assignment."""
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.user_id == user.id,
    ).first()
    if not a:
        raise HTTPException(404)
    info = await save_upload(file, user.tenant_id)
    db.add(MediaUpload(
        tenant_id=user.tenant_id, entity_type="checklist",
        entity_id=assignment_id, uploaded_by_id=user.id, **info,
    ))
    a.proof_url = info["file_path"]
    db.commit()
    return redirect("/checklists")


# ── P6-03: Edit / Delete checklist templates & assignments ────────────────────

@app.post("/checklists/templates/bulk-edit")
def bulk_edit_checklist_templates(
    template_ids: list[str] = Form(...),
    frequency: str = Form(""),
    is_active: str = Form(""),
    evidence_required: str = Form(""),
    assigned_to_user_id: str = Form(""),
    assigned_to_role: str = Form(""),
    assigned_to_dept_id: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    updated = 0
    assignment_rule_changed = (
        assigned_to_user_id != "" or
        assigned_to_role != "" or
        assigned_to_dept_id != ""
    )
    for tmpl_id in template_ids:
        tmpl = db.query(ChecklistTemplate).filter(
            ChecklistTemplate.id == tmpl_id,
            ChecklistTemplate.tenant_id == user.tenant_id,
            ChecklistTemplate.is_deleted == False,
        ).first()
        if not tmpl:
            continue
        if frequency:
            tmpl.frequency = frequency
        if is_active != "":
            tmpl.is_active = (is_active == "1")
        if evidence_required != "":
            tmpl.evidence_required = (evidence_required == "true")
        if assignment_rule_changed:
            tmpl.assigned_to_user_id = assigned_to_user_id or None
            tmpl.assigned_to_role = assigned_to_role or tmpl.assigned_to_role
            tmpl.assigned_to_dept_id = assigned_to_dept_id or None
        # Sync future pending assignments to reflect any changes
        _sync_pending_assignments(db, tmpl, user.tenant_id)
        updated += 1
    db.commit()
    return redirect(f"/checklists?msg=Updated+{updated}+checklists")


@app.post("/checklists/templates/bulk-activate")
def bulk_activate_checklist_templates(
    template_ids: list[str] = Form(...),
    is_active: str = Form("1"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    active = is_active == "1"
    updated = 0
    for tid in template_ids:
        tmpl = db.query(ChecklistTemplate).filter(
            ChecklistTemplate.id == tid,
            ChecklistTemplate.tenant_id == user.tenant_id,
            ChecklistTemplate.is_deleted == False,
        ).first()
        if not tmpl:
            continue
        tmpl.is_active = active
        _sync_pending_assignments(db, tmpl, user.tenant_id)
        updated += 1
    db.commit()
    action = "Activated" if active else "Deactivated"
    return redirect(f"/checklists?msg={action}+{updated}+checklists")


@app.post("/checklists/templates/bulk-delete")
def bulk_delete_checklist_templates(
    template_ids: list[str] = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    deleted = 0
    for tid in template_ids:
        tmpl = db.query(ChecklistTemplate).filter(
            ChecklistTemplate.id == tid,
            ChecklistTemplate.tenant_id == user.tenant_id,
            ChecklistTemplate.is_deleted == False,
        ).first()
        if not tmpl:
            continue
        tmpl.is_deleted = True
        # Soft-delete all future pending assignments
        _sync_pending_assignments(db, tmpl, user.tenant_id)
        deleted += 1
    db.commit()
    return redirect(f"/checklists?msg=Deleted+{deleted}+checklists")


@app.post("/checklists/templates/{template_id}/edit")
def edit_checklist_template(
    template_id: str,
    title: str = Form(...), description: str = Form(""),
    frequency: str = Form("DAILY"),
    evidence_required: bool = Form(False),
    is_active: str = Form("1"),
    reminder_hours_before: int = Form(2),
    reminder_repeat_hours: int = Form(4),
    assigned_to_user_id: str = Form(""),
    assigned_to_dept_id: str = Form(""),
    assigned_to_role: str = Form("EMPLOYEE"),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    tmpl = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.id == template_id,
        ChecklistTemplate.tenant_id == user.tenant_id,
        ChecklistTemplate.is_deleted == False,
    ).first()
    if not tmpl:
        raise HTTPException(404)
    tmpl.title = title
    tmpl.description = description
    tmpl.frequency = frequency
    tmpl.evidence_required = evidence_required
    tmpl.is_active = (is_active == "1")
    tmpl.reminder_hours_before = reminder_hours_before
    tmpl.reminder_repeat_hours = reminder_repeat_hours
    tmpl.assigned_to_user_id = assigned_to_user_id or None
    tmpl.assigned_to_dept_id = assigned_to_dept_id or None
    tmpl.assigned_to_role = assigned_to_role
    # Sync future pending assignments so "Upcoming" reflects the updated settings.
    # Completed/failed/missed (past) assignments are never touched — they are history.
    _sync_pending_assignments(db, tmpl, user.tenant_id)
    db.commit()
    return redirect("/checklists")


def _sync_pending_assignments(db: Session, tmpl, tid: str) -> None:
    """Reconcile future PENDING/IN_PROGRESS assignments against a just-edited template.

    Rules:
    - If template deactivated → soft-delete all future pending assignments.
    - If assignment rule changed → remove assignments for users no longer targeted,
      create assignments for newly targeted users (inheriting the earliest existing due_at).
    - Past/completed/failed assignments are never touched (history).
    """
    now = datetime.utcnow()

    # Deactivated template: clear all future pending assignments
    if not tmpl.is_active:
        db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == tmpl.id,
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.due_at >= now,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ChecklistAssignment.is_deleted == False,
        ).update({"is_deleted": True}, synchronize_session=False)
        return

    # Resolve new target user set
    if tmpl.assigned_to_user_id:
        new_targets = db.query(User).filter(
            User.id == tmpl.assigned_to_user_id,
            User.tenant_id == tid,
            User.is_active == True,
            User.is_deleted == False,
        ).all()
    elif tmpl.assigned_to_dept_id:
        new_targets = db.query(User).filter(
            User.department_id == tmpl.assigned_to_dept_id,
            User.tenant_id == tid,
            User.is_active == True,
            User.is_deleted == False,
        ).all()
    elif tmpl.assigned_to_role:
        new_targets = db.query(User).filter(
            User.role == tmpl.assigned_to_role,
            User.tenant_id == tid,
            User.is_active == True,
            User.is_deleted == False,
        ).all()
    else:
        return

    new_target_ids = {u.id for u in new_targets}

    # Existing future pending assignments
    existing = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == tmpl.id,
        ChecklistAssignment.tenant_id == tid,
        ChecklistAssignment.due_at >= now,
        ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
        ChecklistAssignment.is_deleted == False,
    ).all()

    existing_by_user = {a.user_id: a for a in existing}

    # Also collect users who have an active OVERDUE assignment (past due_at, not yet resolved)
    # so we don't create a duplicate pending on top of it
    overdue_user_ids = {
        a.user_id for a in db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == tmpl.id,
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
            ChecklistAssignment.is_deleted == False,
            ChecklistAssignment.due_at < now,
        ).all()
    }

    # Soft-delete future assignments for users who are no longer targets
    for uid, a in existing_by_user.items():
        if uid not in new_target_ids:
            a.is_deleted = True

    # Create assignments for target users who have no future pending AND no active overdue
    for uid in new_target_ids:
        if uid not in existing_by_user and uid not in overdue_user_ids:
            # Use per-user last completion to compute the correct next due date
            last_done = db.query(ChecklistAssignment).filter(
                ChecklistAssignment.template_id == tmpl.id,
                ChecklistAssignment.user_id == uid,
                ChecklistAssignment.status == "DONE",
                ChecklistAssignment.is_deleted == False,
            ).order_by(ChecklistAssignment.due_at.desc()).first()
            base_dt = last_done.due_at if last_done and last_done.due_at else now
            due = _next_due_from(tmpl.frequency, base_dt)
            db.add(ChecklistAssignment(
                template_id=tmpl.id,
                tenant_id=tid,
                user_id=uid,
                due_at=due,
                evidence_required=bool(tmpl.evidence_required),
            ))


@app.post("/checklists/templates/{template_id}/delete")
def delete_checklist_template(template_id: str,
                               user: User = Depends(require_admin),
                               db: Session = Depends(get_db)):
    tmpl = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.id == template_id,
        ChecklistTemplate.tenant_id == user.tenant_id,
    ).first()
    if not tmpl:
        raise HTTPException(404)
    tmpl.is_deleted = True
    tmpl.is_active = False
    # cascade soft-delete all pending/in-progress assignments so they stop appearing in upcoming
    db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == template_id,
        ChecklistAssignment.tenant_id == user.tenant_id,
        ChecklistAssignment.status.notin_(["DONE", "FAILED"]),
        ChecklistAssignment.is_deleted == False,
    ).update({"is_deleted": True}, synchronize_session=False)
    db.commit()
    return redirect("/checklists")


@app.post("/checklists/repair-schedules")
def repair_checklist_schedules(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Sync all active checklist templates: remove stale assignments, create missing ones."""
    tid = user.tenant_id
    templates = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.tenant_id == tid,
        ChecklistTemplate.is_deleted == False,
        ChecklistTemplate.is_active == True,
    ).all()
    synced = 0
    for tmpl in templates:
        if not getattr(tmpl, "is_recurring", True):
            continue
        before = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == tmpl.id,
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.due_at >= datetime.utcnow(),
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ChecklistAssignment.is_deleted == False,
        ).count()
        _sync_pending_assignments(db, tmpl, tid)
        after = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == tmpl.id,
            ChecklistAssignment.tenant_id == tid,
            ChecklistAssignment.due_at >= datetime.utcnow(),
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ChecklistAssignment.is_deleted == False,
        ).count()
        if before != after:
            synced += 1
    db.commit()
    return redirect(f"/checklists?msg=Synced+{synced}+checklists")


@app.post("/checklists/assignments/{assignment_id}/edit")
def edit_checklist_assignment(
    assignment_id: str, due_at: str = Form(...),
    user: User = Depends(require_manager), db: Session = Depends(get_db),
):
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.tenant_id == user.tenant_id,
        ChecklistAssignment.status == "PENDING",
        ChecklistAssignment.is_deleted == False,
    ).first()
    if not a:
        raise HTTPException(404, "Assignment not found or already started")
    a.due_at = datetime.fromisoformat(due_at)
    db.commit()
    return redirect("/checklists")


@app.post("/checklists/assignments/{assignment_id}/delete")
def delete_checklist_assignment(assignment_id: str,
                                 user: User = Depends(require_admin),
                                 db: Session = Depends(get_db)):
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.tenant_id == user.tenant_id,
    ).first()
    if not a:
        raise HTTPException(404)
    a.is_deleted = True
    db.commit()
    return redirect("/checklists")


@app.post("/checklists/assignments/{assignment_id}/notify")
def notify_checklist_assignment(assignment_id: str,
                                 user: User = Depends(require_manager),
                                 db: Session = Depends(get_db)):
    """Manually trigger a reminder notification for a checklist assignment."""
    from .notifications import create_notification
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.tenant_id == user.tenant_id,
        ChecklistAssignment.is_deleted == False,
    ).first()
    if not a:
        raise HTTPException(404)
    title = a.template.title if a.template else "Checklist Reminder"
    due_str = a.due_at.strftime("%d %b, %I:%M %p") if a.due_at else "—"
    create_notification(
        db, user.tenant_id, a.user_id,
        "CHECKLIST_DUE_SOON",
        f"Reminder: {title}",
        f"Due: {due_str}",
        "/checklists",
    )
    db.commit()
    return redirect("/checklists")


# ── P6-04: Bulk upload checklist templates ────────────────────────────────────

@app.get("/checklists/bulk-template")
def checklist_bulk_template(user: User = Depends(require_admin)):
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["title","description","frequency","assigned_to_role",
                "assigned_to_department","assigned_to_name","assigned_to_phone",
                "evidence_required","is_recurring","reminder_hours_before","reminder_repeat_hours"])
    w.writerow([
        "Mandatory. Checklist title.",
        "Mandatory. Step-by-step instructions.",
        "DAILY / WEEKLY / TWICE_A_MONTH / MONTHLY / QUARTERLY / YEARLY / PER_SHIFT",
        "EMPLOYEE / MANAGER / ADMIN (ignored if assigned_to_name or assigned_to_phone is set).",
        "Optional. Department name (leave blank if assigning to a specific person).",
        "Optional. Full name of the employee to assign to (preferred over phone).",
        "Optional. 10-digit phone of the employee (used only if assigned_to_name is blank).",
        "TRUE or FALSE (default FALSE)",
        "TRUE or FALSE (default TRUE)",
        "Optional. Hours before due to send reminder (default 2).",
        "Optional. Repeat reminder every N hours (default 4).",
    ])
    buf.seek(0)
    from fastapi.responses import StreamingResponse as _SR
    return _SR(iter([buf.read().encode("utf-8-sig")]),
               media_type="text/csv; charset=utf-8",
               headers={"Content-Disposition": "attachment; filename=checklist_template.csv"})


@app.post("/checklists/bulk-upload")
async def checklist_bulk_upload(file: UploadFile = File(...),
                                 user: User = Depends(require_admin),
                                 db: Session = Depends(get_db)):
    import csv, io
    from fastapi.responses import StreamingResponse as _SR
    raw = await file.read()
    # Try UTF-8 first, fall back to Windows-1252 for files saved by Excel
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1252", errors="replace")
    # Normalise frequency aliases so common variants are accepted
    _FREQ_ALIASES = {
        "TWICE A MONTH": "TWICE_A_MONTH",
        "TWICE-A-MONTH": "TWICE_A_MONTH",
        "BI-MONTHLY":    "TWICE_A_MONTH",
        "BIMONTHLY":     "TWICE_A_MONTH",
        "QUATERLY":      "QUARTERLY",    # common misspelling
        "QUATER":        "QUARTERLY",
        "ANNUAL":        "YEARLY",
        "ANNUALLY":      "YEARLY",
    }
    _VALID_FREQS = {"DAILY","WEEKLY","TWICE_A_MONTH","MONTHLY","QUARTERLY","YEARLY","PER_SHIFT"}
    reader = csv.DictReader(io.StringIO(content))
    errors = []
    created = 0
    for i, row in enumerate(reader, start=1):
        title = (row.get("title") or "").strip()
        # Skip description/header-like rows
        if title.lower().startswith("mandatory") or title.lower().startswith("optional"):
            continue
        desc  = (row.get("description") or "").strip()
        # If title is blank but description has content, use description as title
        if not title and desc:
            title = desc
            desc  = ""
        freq_raw = (row.get("frequency") or "DAILY").strip().upper()
        freq = _FREQ_ALIASES.get(freq_raw, freq_raw)
        if not title:
            errors.append((i, "(blank)", "title is required"))
            continue
        if freq not in _VALID_FREQS:
            errors.append((i, title, f"Invalid frequency '{freq_raw}' — valid values: {', '.join(sorted(_VALID_FREQS))}"))
            continue
        # Resolve assignee
        role = (row.get("assigned_to_role") or "EMPLOYEE").strip().upper()
        dept_id = None
        user_id = None
        dept_name = (row.get("assigned_to_department") or "").strip()
        emp_name = (row.get("assigned_to_name") or "").strip()
        phone = (row.get("assigned_to_phone") or "").strip()
        if emp_name:
            from sqlalchemy import func as _func
            u = db.query(User).filter(
                User.tenant_id == user.tenant_id,
                _func.lower(User.name) == emp_name.lower(),
                User.is_deleted == False,
            ).first()
            if not u:
                # Build a helpful list of available names for the error message
                all_names = [r.name for r in db.query(User.name).filter(
                    User.tenant_id == user.tenant_id, User.is_deleted == False).all()]
                errors.append((i, title, f"No employee named '{emp_name}'. Available: {', '.join(sorted(all_names))}"))
                continue
            user_id = u.id
            role = u.role
        elif phone:
            u = db.query(User).filter(User.tenant_id == user.tenant_id,
                                       User.phone == phone, User.is_deleted == False).first()
            if not u:
                errors.append((i, title, f"No user with phone {phone}"))
                continue
            user_id = u.id
            role = u.role
        elif dept_name:
            from .database import Department as _Dept
            d = db.query(_Dept).filter(_Dept.tenant_id == user.tenant_id,
                                        _Dept.name == dept_name,
                                        _Dept.is_deleted == False).first()
            if not d:
                errors.append((i, title, f"Department not found: {dept_name}"))
                continue
            dept_id = d.id
        ev_raw = (row.get("evidence_required") or "").strip().upper()
        ev_req = ev_raw in ("TRUE", "YES", "1", "Y")
        try:
            remind_b = int((row.get("reminder_hours_before") or "2").strip() or 2)
        except ValueError:
            remind_b = 2
        try:
            remind_r = int((row.get("reminder_repeat_hours") or "4").strip() or 4)
        except ValueError:
            remind_r = 4
        is_rec_raw = (row.get("is_recurring") or "TRUE").strip().upper()
        is_rec = is_rec_raw != "FALSE"
        try:
            db.add(ChecklistTemplate(
                tenant_id=user.tenant_id, title=title, description=desc,
                frequency=freq, assigned_to_role=role,
                assigned_to_dept_id=dept_id, assigned_to_user_id=user_id,
                evidence_required=ev_req, is_recurring=is_rec,
                reminder_hours_before=remind_b, reminder_repeat_hours=remind_r,
            ))
            db.flush()
            created += 1
        except Exception as exc:
            db.rollback()
            errors.append((i, title, f"DB error: {exc}"))
    if not errors:
        db.commit()
    else:
        db.rollback()
    if errors:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["row","title","error"])
        for (r, t, e) in errors:
            w.writerow([r, t, e])
        buf.seek(0)
        return _SR(iter([buf.read().encode()]),
                   media_type="text/csv",
                   headers={"Content-Disposition": "attachment; filename=checklist_upload_errors.csv"})
    return redirect(f"/checklists?uploaded={created}")


# ── Employees ─────────────────────────────────────────────────────────────────

@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request, user: User = Depends(require_manager),
                   db: Session = Depends(get_db)):
    tid = user.tenant_id
    if user.role == "MANAGER":
        # Manager sees only their direct reports (+ themselves)
        team_ids = [u.id for u in db.query(User).filter(
            User.manager_id == user.id, User.is_deleted == False).all()]
        team_ids.append(user.id)
        all_users = db.query(User).filter(
            User.id.in_(team_ids), User.is_deleted == False).all()
    else:
        all_users = db.query(User).filter(
            User.tenant_id == tid, User.is_deleted == False).all()
    all_depts = db.query(Department).filter(
        Department.tenant_id == tid, Department.is_deleted == False).all()
    # Deduplicate departments by name for dropdowns (keep first row per name)
    _seen_dnames = set()
    departments_unique = []
    for d in sorted(all_depts, key=lambda x: x.name):
        if d.name not in _seen_dnames:
            _seen_dnames.add(d.name)
            departments_unique.append(d)
    branches = db.query(Branch).filter(
        Branch.tenant_id == tid, Branch.is_deleted == False).all()
    managers = [u for u in db.query(User).filter(
        User.tenant_id == tid, User.is_deleted == False,
        User.role.in_(["ADMIN","MANAGER"])).all()]
    tenant = db.query(Tenant).get(tid)
    can_bulk = has_feature(tenant, "BULK_IMPORT", db)
    # P8-04: KPI strip
    all_for_kpi = db.query(User).filter(User.tenant_id == tid, User.is_deleted == False).all()
    kpi_total      = len(all_for_kpi)
    kpi_active     = sum(1 for u in all_for_kpi if getattr(u, "status", "ACTIVE") == "ACTIVE")
    kpi_terminated = sum(1 for u in all_for_kpi if getattr(u, "status", "ACTIVE") == "TERMINATED")
    kpi_departments = len(departments_unique)
    return templates.TemplateResponse(request, "employees.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "employees": all_users, "departments": departments_unique,
        "branches": branches, "managers": managers,
        "can_bulk": can_bulk,
        "kpi_total": kpi_total, "kpi_active": kpi_active,
        "kpi_terminated": kpi_terminated, "kpi_departments": kpi_departments,
    })

@app.post("/employees/create")
def create_employee(
    name: str = Form(...), phone: str = Form(...), password: str = Form(...),
    role: str = Form("EMPLOYEE"), department_id: str = Form(""),
    manager_id: str = Form(""), branch_id: str = Form(""),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).get(user.tenant_id)
    current_count = db.query(User).filter(
        User.tenant_id == user.tenant_id, User.is_deleted == False).count()
    if _limit_hit(tenant, "max_users", current_count):
        return RedirectResponse("/employees?upgrade=users",
                                status_code=302)
    phone_err = _validate_phone(phone)
    if phone_err:
        return RedirectResponse(f"/employees?error={phone_err.replace(' ', '+')}", status_code=302)
    if db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.phone == phone, User.is_deleted == False,
    ).first():
        return RedirectResponse("/employees?error=Phone+already+registered",
                                status_code=302)
    emp = User(
        tenant_id=user.tenant_id, name=name, phone=phone,
        password_hash=hash_password(password), role=role,
        department_id=department_id or None,
        manager_id=manager_id or None,
        branch_id=branch_id or None,
        employee_id=_next_employee_id(db, user.tenant_id),
        status="ACTIVE",
    )
    db.add(emp)
    db.commit()
    return redirect("/employees")

@app.post("/employees/{emp_id}/assign-manager")
def assign_manager(emp_id: str, manager_id: str = Form(...),
                   user: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Phase 0-A-1: assign manager_id to employee."""
    emp = db.query(User).filter(
        User.id == emp_id, User.tenant_id == user.tenant_id).first()
    if not emp:
        raise HTTPException(404)
    emp.manager_id = manager_id or None
    db.commit()
    return redirect("/employees")

@app.get("/employees/import/template")
def download_csv_template(entity: str = "employees",
                           user: User = Depends(require_admin),
                           db: Session = Depends(get_db)):
    """Phase 0-A-6: download CSV template for bulk import."""
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "BULK_IMPORT", db):
        raise HTTPException(403, "Bulk import requires Professional plan")

    headers = {
        "employees": ["name", "phone", "password", "role", "department_name", "branch_name", "manager_phone", "email", "joining_date", "address"],
        "departments": ["name", "branch_name"],
        "branches": ["name", "address"],
    }
    cols = headers.get(entity, headers["employees"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    if entity == "employees":
        w.writerow(["Mandatory. Full name.", "Mandatory. 10-digit phone (unique).", "Mandatory. Login password (min 6 chars).",
                    "EMPLOYEE / MANAGER / ADMIN (default EMPLOYEE)", "Optional. Must match existing department name exactly.",
                    "Optional. Must match existing branch name exactly.", "Optional. Phone of manager (must exist in system).",
                    "Optional. Email address.", "Optional. YYYY-MM-DD", "Optional. Address."])
    elif entity == "departments":
        w.writerow(["Mandatory. Department name.", "Optional. Must match existing branch name exactly."])
    else:
        w.writerow(["Mandatory. Branch name.", "Optional. Branch address."])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={entity}_template.csv"},
    )

@app.post("/employees/import")
async def bulk_import_employees(file: UploadFile = File(...),
                                user: User = Depends(require_admin),
                                db: Session = Depends(get_db)):
    """Phase 0-A-3: bulk import employees with validation + exception report."""
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "BULK_IMPORT", db):
        raise HTTPException(403, "Bulk import requires Professional plan")

    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))

    from datetime import date as _date

    def _parse_date(s: str):
        """Accept YYYY-MM-DD, M/D/YYYY, MM/DD/YYYY, D-M-YYYY etc."""
        s = s.strip()
        if not s:
            return None
        # ISO first
        try:
            return _date.fromisoformat(s)
        except ValueError:
            pass
        # slash-separated (M/D/YYYY or MM/DD/YYYY)
        for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        return None

    # Build branch lookup for optional branch_name column
    _branch_lkp = {b.name.strip().lower(): b.id
                   for b in db.query(Branch).filter(
                       Branch.tenant_id == user.tenant_id,
                       Branch.is_deleted == False).all()}

    errors, imported = [], 0
    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        phone = (row.get("phone") or "").strip()
        password = (row.get("password") or "").strip()
        role = (row.get("role") or "EMPLOYEE").strip().upper()

        # Skip description rows
        if name.lower().startswith("mandatory") or name.lower().startswith("optional"):
            continue

        if not name:
            errors.append({"row": i, "error": "name is required", "data": dict(row)})
            continue
        if not phone:
            errors.append({"row": i, "error": "phone is required", "data": dict(row)})
            continue
        if not password:
            errors.append({"row": i, "error": "password is required", "data": dict(row)})
            continue
        if role not in ("EMPLOYEE", "MANAGER", "ADMIN"):
            errors.append({"row": i, "error": f"invalid role '{role}'", "data": dict(row)})
            continue
        if db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.phone == phone, User.is_deleted == False,
        ).first():
            errors.append({"row": i, "error": f"phone {phone} already exists", "data": dict(row)})
            continue

        # Resolve optional dept
        dept_id = None
        dept_name = (row.get("department_name") or "").strip()
        if dept_name:
            dept = db.query(Department).filter(
                Department.tenant_id == user.tenant_id,
                Department.name == dept_name,
                Department.is_deleted == False,
            ).first()
            if dept:
                dept_id = dept.id

        # Resolve optional branch
        branch_id = None
        branch_name = (row.get("branch_name") or "").strip()
        if branch_name:
            branch_id = _branch_lkp.get(branch_name.lower())

        # Resolve optional manager
        mgr_id = None
        mgr_phone = (row.get("manager_phone") or "").strip()
        if mgr_phone:
            mgr = db.query(User).filter(
                User.tenant_id == user.tenant_id,
                User.phone == mgr_phone, User.is_deleted == False,
            ).first()
            if mgr:
                mgr_id = mgr.id

        jdate = _parse_date(row.get("joining_date") or "")

        db.add(User(
            tenant_id=user.tenant_id, name=name, phone=phone,
            email=(row.get("email") or "").strip() or None,
            password_hash=hash_password(password), role=role,
            department_id=dept_id,
            branch_id=branch_id,
            manager_id=mgr_id,
            address=(row.get("address") or "").strip() or None,
            joining_date=jdate,
            status="ACTIVE",
            employee_id=_next_employee_id(db, user.tenant_id),
        ))
        imported += 1

    db.commit()

    if errors:
        # Return downloadable exception report
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["row", "error", "name", "phone", "role"])
        w.writeheader()
        for e in errors:
            w.writerow({
                "row": e["row"], "error": e["error"],
                "name": e["data"].get("name", ""),
                "phone": e["data"].get("phone", ""),
                "role": e["data"].get("role", ""),
            })
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=import_errors.csv",
                     "X-Imported": str(imported)},
        )

    return redirect(f"/employees?imported={imported}")


@app.post("/employees/{emp_id}/reset-password")
def reset_employee_password(
    emp_id: str,
    temp_password: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin generates a temporary password for an employee (Forgot Password — Option C)."""
    emp = db.query(User).filter(
        User.id == emp_id,
        User.tenant_id == user.tenant_id,
        User.is_deleted == False,
    ).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    emp.password_hash = hash_password(temp_password)
    db.commit()
    return redirect(f"/employees?msg=Password+reset+for+{emp.name}")


# ── P8-01 / P8-06: Edit employee profile ─────────────────────────────────────
@app.post("/employees/{emp_id}/edit")
def edit_employee(
    emp_id: str,
    name: str = Form(...), phone: str = Form(...),
    email: str = Form(""), role: str = Form(...),
    department_id: str = Form(""), manager_id: str = Form(""),
    joining_date: str = Form(""), address: str = Form(""),
    branch_id: str = Form(""),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    phone_err = _validate_phone(phone)
    if phone_err:
        return redirect(f"/employees?error={phone_err.replace(' ', '+')}")
    email_err = _validate_email(email)
    if email_err:
        return redirect(f"/employees?error={email_err.replace(' ', '+')}")
    emp = db.query(User).filter(
        User.id == emp_id, User.tenant_id == user.tenant_id, User.is_deleted == False,
    ).first()
    if not emp:
        raise HTTPException(404)
    emp.name = name
    emp.phone = phone
    emp.email = email or None
    emp.role = role
    emp.department_id = department_id or None
    emp.manager_id = manager_id or None
    emp.branch_id = branch_id or None
    emp.address = address or None
    from datetime import date as _date
    if joining_date:
        try:
            emp.joining_date = _date.fromisoformat(joining_date)
        except ValueError:
            pass
    db.commit()
    return redirect(f"/employees?msg=Profile+updated+for+{emp.name}")


# ── P8-02: Open-work count (JSON) for terminate modal ────────────────────────
@app.get("/employees/{emp_id}/open-work")
def emp_open_work(emp_id: str, user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    tid = user.tenant_id
    target = db.query(User).filter(
        User.id == emp_id, User.tenant_id == tid, User.is_deleted == False,
    ).first()
    if not target:
        raise HTTPException(404)
    from sqlalchemy import or_
    _open_statuses = ["OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "HELP_REQUESTED", "DONE"]
    # primary assignee OR helper assignee
    _helper_tids = [
        row.ticket_id for row in
        db.query(TicketAssignee.ticket_id).filter(TicketAssignee.user_id == emp_id).all()
    ]
    ticket_rows = (
        db.query(Ticket)
        .filter(
            Ticket.tenant_id == tid,
            Ticket.is_deleted == False,
            Ticket.status.in_(_open_statuses),
            or_(
                Ticket.current_assignee_id == emp_id,
                Ticket.id.in_(_helper_tids),
            ),
        )
        .all()
    )
    cl_rows = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.user_id == emp_id,
        ChecklistAssignment.is_deleted == False,
        ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
    ).all()
    from .database import FMSTicketHelper as _FTH
    fms_rows = (
        db.query(FMSTicket)
        .join(_FTH, _FTH.ticket_id == FMSTicket.id)
        .filter(_FTH.user_id == emp_id,
                FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
                FMSTicket.status.notin_(["COMPLETED", "CLOSED"]))
        .all()
    )
    return JSONResponse({
        "tickets": len(ticket_rows),
        "checklists": len(cl_rows),
        "fms": len(fms_rows),
        "ticket_items": [{"id": t.id, "title": t.title, "status": t.status} for t in ticket_rows],
        "checklist_items": [{"id": c.id, "title": c.checklist.title if c.checklist else str(c.id), "status": c.status} for c in cl_rows],
        "fms_items": [{"id": f.id, "title": f.title or f"FMS #{f.id[:8]}", "status": f.status} for f in fms_rows],
    })


# ── P8-02: Terminate employee with migration flow ─────────────────────────────
@app.post("/employees/{emp_id}/terminate")
def terminate_employee(
    emp_id: str,
    ticket_reassign_to: str = Form(""),
    checklist_reassign_to: str = Form(""),
    fms_reassign_to: str = Form(""),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    tid = user.tenant_id
    emp = db.query(User).filter(
        User.id == emp_id, User.tenant_id == tid, User.is_deleted == False,
    ).first()
    if not emp:
        raise HTTPException(404)
    if emp.id == user.id:
        return redirect("/employees?error=Cannot+terminate+yourself")
    if getattr(emp, "status", "ACTIVE") == "TERMINATED":
        return redirect("/employees?error=Employee+already+terminated")

    OPEN_STATUSES = ["OPEN", "ACKNOWLEDGED", "IN_PROGRESS"]

    if ticket_reassign_to:
        # Validate reassignee belongs to this tenant
        reassignee = db.query(User).filter(
            User.id == ticket_reassign_to, User.tenant_id == tid, User.is_deleted == False,
        ).first()
        if reassignee:
            # 1. Reassign primary assignee on open tickets (the main case — was missing)
            open_tickets = db.query(Ticket).filter(
                Ticket.tenant_id == tid,
                Ticket.current_assignee_id == emp_id,
                Ticket.is_deleted == False,
                Ticket.status.in_(OPEN_STATUSES),
            ).all()
            for t in open_tickets:
                t.current_assignee_id = ticket_reassign_to
                log_event(db, t.id, user.id, "REASSIGNED",
                          f"Bulk reassign on termination of {emp.name} → {reassignee.name}")

            # 2. Reassign helper/co-assignee rows on open tickets (tenant-scoped)
            helper_rows = (
                db.query(TicketAssignee)
                .join(Ticket, Ticket.id == TicketAssignee.ticket_id)
                .filter(
                    TicketAssignee.user_id == emp_id,
                    Ticket.tenant_id == tid,
                    Ticket.is_deleted == False,
                    Ticket.status.in_(OPEN_STATUSES),
                )
                .all()
            )
            for ta in helper_rows:
                ta.user_id = ticket_reassign_to

    if checklist_reassign_to:
        reassignee_cl = db.query(User).filter(
            User.id == checklist_reassign_to, User.tenant_id == tid, User.is_deleted == False,
        ).first()
        if reassignee_cl:
            for cl in db.query(ChecklistAssignment).filter(
                ChecklistAssignment.user_id == emp_id,
                ChecklistAssignment.tenant_id == tid,
                ChecklistAssignment.is_deleted == False,
                ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ).all():
                cl.user_id = checklist_reassign_to

    if fms_reassign_to:
        from .database import FMSTicketHelper as _FTH
        reassignee_fms = db.query(User).filter(
            User.id == fms_reassign_to, User.tenant_id == tid, User.is_deleted == False,
        ).first()
        if reassignee_fms:
            # Reassign primary assignee on open FMS tickets
            open_fms = db.query(FMSTicket).filter(
                FMSTicket.tenant_id == tid,
                FMSTicket.current_assignee_id == emp_id,
                FMSTicket.is_deleted == False,
                FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
            ).all()
            for ft in open_fms:
                ft.current_assignee_id = fms_reassign_to
                db.add(FMSEvent(
                    ticket_id=ft.id, actor_id=user.id, event_type="REASSIGNED",
                    detail=f"Bulk reassign on termination of {emp.name} → {reassignee_fms.name}",
                ))

            # Reassign FMS helper rows (tenant-scoped)
            for fh in (
                db.query(_FTH)
                .filter(_FTH.user_id == emp_id)
                .join(FMSTicket, FMSTicket.id == _FTH.ticket_id)
                .filter(FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
                        FMSTicket.status.notin_(["COMPLETED", "CLOSED"]))
                .all()
            ):
                fh.user_id = fms_reassign_to

    emp.status = "TERMINATED"
    emp.is_active = False
    emp.terminated_at = datetime.utcnow()
    db.commit()
    return redirect(f"/employees?msg={emp.name}+has+been+terminated")


# ── P8-06: Soft-delete employee ───────────────────────────────────────────────
@app.post("/employees/{emp_id}/delete")
def delete_employee(
    emp_id: str, user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    emp = db.query(User).filter(
        User.id == emp_id, User.tenant_id == user.tenant_id, User.is_deleted == False,
    ).first()
    if not emp:
        raise HTTPException(404)
    if emp.id == user.id:
        return redirect("/employees?error=Cannot+delete+yourself")
    emp.is_deleted = True
    emp.is_active = False
    db.commit()
    return redirect("/employees?msg=Employee+removed")


# ── WhatsApp: toggle mobile_verified on an employee ──────────────────────────
@app.post("/employees/{emp_id}/toggle-validated")
def toggle_employee_validated(
    emp_id: str, request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    emp = db.query(User).filter(
        User.id == emp_id,
        User.tenant_id == user.tenant_id,
        User.is_deleted == False,
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if emp.mobile_verified:
        emp.mobile_verified = False
        emp.mobile_verified_at = None
        emp.mobile_verified_by = None
    else:
        emp.mobile_verified = True
        emp.mobile_verified_at = datetime.utcnow()
        emp.mobile_verified_by = user.id
    db.commit()
    return RedirectResponse("/employees", status_code=303)


# ── WhatsApp: resend a failed message log entry ───────────────────────────────
@app.post("/whatsapp-log/{log_id}/resend")
def resend_whatsapp(
    log_id: str, request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .database import WhatsAppMessageLog
    from .services.msg91 import send_whatsapp_template
    import json as _json_resend

    log = db.query(WhatsAppMessageLog).filter(
        WhatsAppMessageLog.id == log_id,
        WhatsAppMessageLog.tenant_id == user.tenant_id,
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Log entry not found")
    if log.status != "FAILED":
        raise HTTPException(status_code=400, detail="Only failed sends can be resent")

    variables = _json_resend.loads(log.variables_json)
    success, error = send_whatsapp_template(log.recipient_phone, log.template_name, variables)
    log.status = "SENT" if success else "FAILED"
    log.error_message = error
    log.attempt_count += 1
    log.last_attempted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


# ── P8-03: Per-employee performance dashboard ─────────────────────────────────
@app.get("/employees/{emp_id}/performance", response_class=HTMLResponse)
def employee_performance(
    emp_id: str, request: Request, period: str = "30d",
    user: User = Depends(require_manager), db: Session = Depends(get_db),
):
    tid = user.tenant_id
    emp = db.query(User).filter(
        User.id == emp_id, User.tenant_id == tid, User.is_deleted == False,
    ).first()
    if not emp:
        raise HTTPException(404)
    if user.role == "MANAGER" and emp.manager_id != user.id and emp.id != user.id:
        raise HTTPException(403)

    now = datetime.utcnow()
    since_map = {
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
        "90d": now - timedelta(days=90),
        "all": datetime(2000, 1, 1),
    }
    since = since_map.get(period, since_map["30d"])

    ticket_kpis = get_employee_kpis(db, emp_id, tid)

    cl_base = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.user_id == emp_id,
        ChecklistAssignment.is_deleted == False,
        ChecklistAssignment.created_at >= since,
    )
    cl_total   = cl_base.count()
    cl_done    = cl_base.filter(ChecklistAssignment.status == "DONE").count()
    cl_overdue = cl_base.filter(ChecklistAssignment.status == "OVERDUE").count()
    cl_compliance = round(cl_done / cl_total * 100) if cl_total else 0

    from .database import FMSTicketHelper as _FTH
    fms_base = (
        db.query(_FTH)
        .filter(_FTH.user_id == emp_id)
        .join(FMSTicket, FMSTicket.id == _FTH.ticket_id)
        .filter(FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
                FMSTicket.created_at >= since)
    )
    fms_total = fms_base.count()
    fms_done  = (
        db.query(_FTH)
        .filter(_FTH.user_id == emp_id)
        .join(FMSTicket, FMSTicket.id == _FTH.ticket_id)
        .filter(FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
                FMSTicket.created_at >= since,
                FMSTicket.status.in_(["COMPLETED", "CLOSED"]))
        .count()
    )
    fms_on_time = (
        db.query(_FTH)
        .filter(_FTH.user_id == emp_id)
        .join(FMSTicket, FMSTicket.id == _FTH.ticket_id)
        .filter(FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
                FMSTicket.created_at >= since,
                FMSTicket.status.in_(["COMPLETED", "CLOSED"]),
                FMSTicket.due_at != None,
                FMSTicket.completed_at != None,
                FMSTicket.completed_at <= FMSTicket.due_at)
        .count()
    )
    fms_on_time_pct  = round(fms_on_time / fms_done * 100) if fms_done > 0 else 0
    fms_complete_pct = round(fms_done / fms_total * 100)   if fms_total > 0 else 0

    # ── Score components (transparent calculation) ──────────────────────────
    _closed_30d   = ticket_kpis.get("closed_30d", 0)
    _active_count = ticket_kpis.get("active_count", 0)
    ticket_score = (ticket_kpis.get("on_time_rate", 0) if _closed_30d > 0
                    else (0 if _active_count > 0 else None))
    cl_score     = cl_compliance if cl_total > 0 else None
    # FMS score: prefer on-time rate if due_at data exists, fall back to completion rate
    fms_score    = fms_on_time_pct if (fms_done > 0 and fms_on_time > 0) else (fms_complete_pct if fms_total > 0 else None)

    # Always include all 3 components; value=None means no data yet (shown as placeholder)
    fms_metric = "On-Time Rate" if (fms_done > 0 and fms_on_time > 0) else "Completion Rate"
    fms_detail = (f"{fms_on_time} of {fms_done} on time" if (fms_done > 0 and fms_on_time > 0)
                  else (f"{fms_done} of {fms_total} completed" if fms_total > 0 else "No flow ticket data yet"))
    score_components = [
        {
            "label": "Tickets",
            "metric": "On-Time Rate",
            "value": ticket_score,
            "detail": (f"{ticket_kpis.get('on_time_rate',0)}% of {_closed_30d} closed"
                       if _closed_30d > 0 else (f"{_active_count} active — no closed tickets yet"
                       if _active_count > 0 else "No tickets assigned yet")),
            "color": "#3b82f6",
        },
        {
            "label": "Checklists",
            "metric": "Compliance Rate",
            "value": cl_score,
            "detail": f"{cl_done} of {cl_total} completed" if cl_total > 0 else "No checklists assigned yet",
            "color": "#10b981",
        },
        {
            "label": "Flow Tickets",
            "metric": fms_metric,
            "value": fms_score,
            "detail": fms_detail,
            "color": "#8b5cf6",
        },
    ]

    active_components = [c for c in score_components if c["value"] is not None]
    overall_score = round(sum(c["value"] for c in active_components) / len(active_components)) if active_components else 0

    return templates.TemplateResponse(request, "employee_performance.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "emp": emp, "period": period,
        "ticket_kpis": ticket_kpis,
        "cl_total": cl_total, "cl_done": cl_done,
        "cl_overdue": cl_overdue, "cl_compliance": cl_compliance,
        "fms_total": fms_total, "fms_done": fms_done,
        "fms_on_time": fms_on_time, "fms_on_time_pct": fms_on_time_pct,
        "fms_complete_pct": fms_complete_pct,
        "overall_score": overall_score,
        "score_components": score_components,
        "managers": db.query(User).filter(
            User.tenant_id == tid, User.is_deleted == False,
            User.role.in_(["ADMIN", "MANAGER"]),
        ).all(),
    })


@app.post("/departments/import")
async def bulk_import_departments(file: UploadFile = File(...),
                                   user: User = Depends(require_admin),
                                   db: Session = Depends(get_db)):
    """Phase 0-A-4: bulk import departments."""
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "BULK_IMPORT", db):
        raise HTTPException(403, "Requires Professional plan")
    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    count = 0
    # Build branch name → id lookup for this tenant
    _branch_lookup = {b.name.strip().lower(): b.id for b in db.query(Branch).filter(
        Branch.tenant_id == user.tenant_id, Branch.is_deleted == False).all()}
    for row in reader:
        name = (row.get("name") or "").strip()
        if not name or name.lower().startswith("mandatory") or name.lower().startswith("optional"):
            continue
        branch_name = (row.get("branch_name") or "").strip()
        branch_id = _branch_lookup.get(branch_name.lower()) if branch_name else None
        # Skip exact duplicates (same name + branch_id already exists)
        exists = db.query(Department).filter(
            Department.tenant_id == user.tenant_id,
            Department.name == name,
            Department.branch_id == branch_id,
            Department.is_deleted == False,
        ).first()
        if exists:
            continue
        db.add(Department(tenant_id=user.tenant_id, name=name, branch_id=branch_id))
        count += 1
    db.commit()
    return redirect(f"/setup?imported_depts={count}")

@app.post("/branches/import")
async def bulk_import_branches(file: UploadFile = File(...),
                                user: User = Depends(require_admin),
                                db: Session = Depends(get_db)):
    """Phase 0-A-5: bulk import branches."""
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "BULK_IMPORT", db):
        raise HTTPException(403, "Requires Professional plan")
    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    count = 0
    for row in reader:
        name = (row.get("name") or "").strip()
        if not name or name.lower().startswith("mandatory") or name.lower().startswith("optional"):
            continue
        db.add(Branch(tenant_id=user.tenant_id, name=name,
                      address=(row.get("address") or "").strip()))
        count += 1
    db.commit()
    return redirect(f"/setup?imported_branches={count}")


# ── Setup ─────────────────────────────────────────────────────────────────────

@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request, user: User = Depends(require_admin),
          db: Session = Depends(get_db)):
    from .constants import PLAN_LIMITS, PLAN_LABELS
    branches = db.query(Branch).filter(
        Branch.tenant_id == user.tenant_id, Branch.is_deleted == False).all()
    departments = db.query(Department).filter(
        Department.tenant_id == user.tenant_id,
        Department.is_deleted == False).all()
    tenant = db.query(Tenant).get(user.tenant_id)
    emp_count = db.query(User).filter(
        User.tenant_id == user.tenant_id, User.is_deleted == False).count()
    plan = tenant.plan or "STARTER"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["STARTER"])
    usage = {
        "max_users": emp_count,
        "max_branches": len(branches),
    }
    # Group departments by name, collecting branch names per group
    from collections import defaultdict as _dd
    _branch_map = {b.id: b for b in branches}
    _dept_by_name = _dd(list)
    for d in departments:
        _dept_by_name[d.name].append(d)
    departments_grouped = []
    for dname, dlist in sorted(_dept_by_name.items()):
        # Deduplicate branch names within the group
        seen_bnames = set()
        dept_branches = []
        first_bid = ""
        for d in dlist:
            if d.branch_id and d.branch_id in _branch_map:
                bname = _branch_map[d.branch_id].name
                if bname not in seen_bnames:
                    seen_bnames.add(bname)
                    dept_branches.append(bname)
                if not first_bid:
                    first_bid = d.branch_id
        departments_grouped.append({
            "name": dname,
            "branches": dept_branches,
            "branch_id": first_bid,
            "ids": [d.id for d in dlist],
            "id": dlist[0].id,
        })

    return templates.TemplateResponse(request, "setup.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "branches": branches, "departments": departments,
        "departments_grouped": departments_grouped,
        "distinct_dept_count": len(departments_grouped),
        "tenant": tenant, "employee_count": emp_count,
        "can_bulk": has_feature(tenant, "BULK_IMPORT", db),
        "plan_limits": limits, "plan_usage": usage,
        "all_plan_limits": PLAN_LIMITS, "plan_labels": PLAN_LABELS,
        "current_plan": plan,
        "checklist_notif_hours": getattr(tenant, "checklist_notif_hours", None) or "8,13,18",
        "checklist_overdue_hour": getattr(tenant, "checklist_overdue_hour", None) or "",
    })


@app.post("/setup/checklist-notifications")
def setup_checklist_notifications(
    notif_hours: str = Form("8,13,18"),
    overdue_hour: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save checklist notification hours and optional overdue WhatsApp hour."""
    import re as _re
    # Sanitise reminder hours
    raw = [h.strip() for h in notif_hours.replace(";", ",").split(",")]
    valid = []
    for h in raw:
        if _re.fullmatch(r"\d{1,2}", h) and 0 <= int(h) <= 23:
            valid.append(str(int(h)))
    if not valid:
        valid = ["8", "13", "18"]
    # Sanitise overdue hour — blank or invalid → None (disabled)
    overdue_clean = overdue_hour.strip()
    if overdue_clean and _re.fullmatch(r"\d{1,2}", overdue_clean) and 0 <= int(overdue_clean) <= 23:
        tenant_overdue_hour = str(int(overdue_clean))
    else:
        tenant_overdue_hour = None
    tenant = db.query(Tenant).get(user.tenant_id)
    if tenant:
        tenant.checklist_notif_hours = ",".join(valid)
        tenant.checklist_overdue_hour = tenant_overdue_hour
        db.commit()
    return redirect("/setup?open=notifications&saved=1")


@app.post("/setup/branch")
def add_branch(name: str = Form(...), location: str = Form(""),
               redirect_to: str = Form("/setup?open=branch"),
               user: User = Depends(require_admin), db: Session = Depends(get_db)):
    tenant = db.query(Tenant).get(user.tenant_id)
    current = db.query(Branch).filter(Branch.tenant_id == user.tenant_id,
                                       Branch.is_deleted == False).count()
    if _limit_hit(tenant, "max_branches", current):
        base = redirect_to.split("?")[0]
        return redirect(f"{base}?upgrade=branch&open=branch")
    db.add(Branch(tenant_id=user.tenant_id, name=name, address=location))
    db.commit()
    return redirect(redirect_to)

@app.post("/setup/branch/{branch_id}/edit")
def edit_branch(branch_id: str, name: str = Form(...), location: str = Form(""),
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    b = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id).first()
    if b:
        b.name = name.strip()
        b.address = location.strip()
        db.commit()
    return redirect("/setup")

@app.post("/setup/branch/{branch_id}/delete")
def delete_branch(branch_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    b = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id).first()
    if b:
        b.is_deleted = True
        db.commit()
    return redirect("/setup")

@app.post("/setup/department")
async def add_department(
    request: Request,
    redirect_to: str = Form("/setup?open=dept"),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    form = await request.form()
    name = (form.get("name") or "").strip()
    branch_ids = form.getlist("branch_ids")
    if not name:
        return redirect(redirect_to)
    if branch_ids:
        for bid in branch_ids:
            bid = bid.strip()
            if bid:
                db.add(Department(tenant_id=user.tenant_id, name=name, branch_id=bid))
    else:
        db.add(Department(tenant_id=user.tenant_id, name=name, branch_id=None))
    db.commit()
    return redirect(redirect_to)

@app.post("/setup/department/{dept_id}/edit")
async def edit_department(
    request: Request,
    dept_id: str,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    form = await request.form()
    name = (form.get("name") or "").strip()
    branch_ids = [b.strip() for b in form.getlist("branch_ids") if b.strip()]
    d = db.query(Department).filter(Department.id == dept_id, Department.tenant_id == user.tenant_id).first()
    if d and name:
        old_name = d.name
        # Remove all existing dept records with this name
        db.query(Department).filter(
            Department.tenant_id == user.tenant_id,
            Department.name == old_name,
            Department.is_deleted == False,
        ).update({"is_deleted": True})
        db.flush()
        # Re-create with new name and selected branches
        if branch_ids:
            for bid in branch_ids:
                db.add(Department(tenant_id=user.tenant_id, name=name, branch_id=bid))
        else:
            db.add(Department(tenant_id=user.tenant_id, name=name, branch_id=None))
        db.commit()
    return redirect("/setup")

@app.post("/setup/department/{dept_id}/delete")
def delete_department(dept_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    d = db.query(Department).filter(Department.id == dept_id, Department.tenant_id == user.tenant_id).first()
    if d:
        # Delete all departments with this name across branches
        db.query(Department).filter(
            Department.tenant_id == user.tenant_id,
            Department.name == d.name,
            Department.is_deleted == False
        ).update({"is_deleted": True})
        db.commit()
    return redirect("/setup")

@app.get("/setup/wizard", response_class=HTMLResponse)
def onboarding_wizard(request: Request, step: Optional[int] = None,
                      user: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """Guided onboarding wizard — auto-continues from where user left off."""
    tenant = db.query(Tenant).get(user.tenant_id)
    branches = db.query(Branch).filter(
        Branch.tenant_id == user.tenant_id, Branch.is_deleted == False).all()
    departments = db.query(Department).filter(
        Department.tenant_id == user.tenant_id,
        Department.is_deleted == False).all()
    employees = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.is_deleted == False, User.is_active == True).all()
    checklist_count = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.tenant_id == user.tenant_id,
        ChecklistTemplate.is_deleted == False).count()

    # Auto-detect which step to show based on completion state
    if step is None:
        if not branches:
            step = 2
        elif not departments:
            step = 3
        elif len(employees) <= 1:   # only the admin themselves
            step = 4
        elif checklist_count == 0:
            step = 5
        else:
            step = 6  # all done

    # Compute completion flags for progress bar
    completed = {
        1: True,
        2: len(branches) > 0,
        3: len(departments) > 0,
        4: len(employees) > 1,
        5: checklist_count > 0,
    }

    return templates.TemplateResponse(request, "onboarding_wizard.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        "tenant": tenant, "step": step,
        "branches": branches, "departments": departments,
        "employees": employees, "checklist_count": checklist_count,
        "completed": completed,
    })


# ── Notifications ─────────────────────────────────────────────────────────────

@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Phase 0-D-4: in-app notification centre."""
    notifs = db.query(Notification).filter(
        Notification.user_id == user.id,
    ).order_by(Notification.created_at.desc()).limit(100).all()
    return templates.TemplateResponse(request, "notifications.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "notifications": notifs,
    })

@app.post("/notifications/read/{notif_id}")
def mark_read(notif_id: str, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    n = db.query(Notification).filter(
        Notification.id == notif_id, Notification.user_id == user.id).first()
    if n:
        n.is_read = True
        db.commit()
    return redirect("/notifications")

@app.post("/notifications/read-all")
def mark_all_read(user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False,
    ).update({"is_read": True})
    db.commit()
    return redirect("/notifications")


# ── KPI / Analytics ───────────────────────────────────────────────────────────

@app.get("/kpi", response_class=HTMLResponse)
def kpi_page(request: Request, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Phase 0-E-6 / 0-G-1..5: employee self-view KPI tab."""
    tid = user.tenant_id
    kpis = get_employee_kpis(db, user.id, tid)
    org_avg = get_org_avg_tat(db, tid)
    tenant = db.query(Tenant).get(tid)

    admin_kpis = None
    if user.role in ("ADMIN", "MANAGER") and has_feature(tenant, "KPI_CHARTS_ADMIN", db):
        admin_kpis = get_all_employee_kpis(db, tid)

    return templates.TemplateResponse(request, "kpi.html", {
        "user": user, "unread": _unread_count(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "kpis": kpis, "org_avg_tat": org_avg,
        "admin_kpis": admin_kpis,
        "can_export": has_feature(tenant, "CSV_EXPORT", db),
    })

@app.get("/analytics/export")
def export_csv(export_type: str = "tickets",
               user: User = Depends(require_manager),
               db: Session = Depends(get_db)):
    """Phase 0-E-5: CSV export from dashboard."""
    tid = user.tenant_id
    tenant = db.query(Tenant).get(tid)
    if not has_feature(tenant, "CSV_EXPORT", db):
        raise HTTPException(403, "CSV export requires Professional plan")

    buf = io.StringIO()
    if export_type == "tickets":
        w = csv.writer(buf)
        w.writerow(["ID", "Title", "Priority", "Status", "Assignee",
                    "Created", "Due", "Closed", "Type"])
        tickets = db.query(Ticket).filter(
            Ticket.tenant_id == tid, Ticket.is_deleted == False).all()
        for t in tickets:
            w.writerow([
                t.id, t.title, t.priority, t.status,
                t.current_assignee.name if t.current_assignee else "",
                t.created_at.strftime("%Y-%m-%d") if t.created_at else "",
                t.due_at.strftime("%Y-%m-%d") if t.due_at else "",
                t.closed_at.strftime("%Y-%m-%d") if t.closed_at else "",
                t.ticket_type,
            ])
        fname = "tickets_export.csv"
    elif export_type == "kpis":
        w = csv.writer(buf)
        w.writerow(["Name", "Role", "Dept", "Compliance%",
                    "AvgTaT_h", "OnTime%", "ActiveTickets"])
        for emp_kpi in get_all_employee_kpis(db, tid):
            u = emp_kpi["user"]
            w.writerow([
                u.name, u.role,
                u.department.name if u.department else "",
                emp_kpi["compliance_rate"],
                emp_kpi["avg_tat_hours"],
                emp_kpi["on_time_rate"],
                emp_kpi["active_count"],
            ])
        fname = "kpi_export.csv"
    else:
        w = csv.writer(buf)
        w.writerow(["Title", "Frequency", "User", "Due", "Status", "Completed"])
        assignments = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tid).all()
        for a in assignments:
            w.writerow([
                a.template.title if a.template else "",
                a.template.frequency if a.template else "",
                a.user.name if a.user else "",
                a.due_at.strftime("%Y-%m-%d %H:%M") if a.due_at else "",
                a.status,
                a.completed_at.strftime("%Y-%m-%d %H:%M") if a.completed_at else "",
            ])
        fname = "checklists_export.csv"

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ── Label Configuration — Phase 0-J ──────────────────────────────────────────

@app.get("/settings/labels", response_class=HTMLResponse)
def labels_page(request: Request, user: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """Tenant admin label configuration page."""
    tenant = db.query(Tenant).get(user.tenant_id)
    row = db.query(TenantLabelConfig).filter(
        TenantLabelConfig.tenant_id == user.tenant_id).first()
    L = _L(db, user)
    return templates.TemplateResponse(request, "settings_labels.html", {
        "user": user, "unread": _unread_count(db, user), "L": L,
        **_nav_ctx(db, user),
        "tenant": tenant, "row": row,
        "industry_names": INDUSTRY_NAMES,
        "defaults": {
            "ticket_s": "Ticket", "ticket_p": "Tickets",
            "checklist_s": "Checklist", "checklist_p": "Checklists",
            "branch_s": "Branch", "branch_p": "Branches",
            "department_s": "Department", "department_p": "Departments",
            "employee_s": "Employee", "employee_p": "Employees",
        },
    })


@app.post("/settings/labels")
def save_labels(
    request: Request,
    ticket_s: str = Form(...),    ticket_p: str = Form(...),
    checklist_s: str = Form(...), checklist_p: str = Form(...),
    branch_s: str = Form(...),    branch_p: str = Form(...),
    department_s: str = Form(...),department_p: str = Form(...),
    employee_s: str = Form(...),  employee_p: str = Form(...),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Save custom label overrides for this tenant."""
    def _clean(s: str, default: str) -> Optional[str]:
        s = s.strip()
        return None if s == default else s or None

    row = db.query(TenantLabelConfig).filter(
        TenantLabelConfig.tenant_id == user.tenant_id).first()
    if row is None:
        row = TenantLabelConfig(tenant_id=user.tenant_id)
        db.add(row)

    row.ticket_s     = _clean(ticket_s,     "Ticket")
    row.ticket_p     = _clean(ticket_p,     "Tickets")
    row.checklist_s  = _clean(checklist_s,  "Checklist")
    row.checklist_p  = _clean(checklist_p,  "Checklists")
    row.branch_s     = _clean(branch_s,     "Branch")
    row.branch_p     = _clean(branch_p,     "Branches")
    row.department_s = _clean(department_s, "Department")
    row.department_p = _clean(department_p, "Departments")
    row.employee_s   = _clean(employee_s,   "Employee")
    row.employee_p   = _clean(employee_p,   "Employees")
    row.updated_at   = datetime.utcnow()
    db.commit()
    return redirect("/settings/labels?msg=saved")


@app.post("/settings/labels/preset")
def apply_preset(
    industry: str = Form(...),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Apply an industry preset to this tenant's label config."""
    overrides = INDUSTRY_PRESETS.get(industry, {})
    row = db.query(TenantLabelConfig).filter(
        TenantLabelConfig.tenant_id == user.tenant_id).first()
    if row is None:
        row = TenantLabelConfig(tenant_id=user.tenant_id)
        db.add(row)

    def _get(concept: str, idx: int) -> Optional[str]:
        entry = overrides.get(concept)
        return entry[idx] if entry else None

    row.ticket_s     = _get("ticket",     0)
    row.ticket_p     = _get("ticket",     1)
    row.checklist_s  = _get("checklist",  0)
    row.checklist_p  = _get("checklist",  1)
    row.branch_s     = _get("branch",     0)
    row.branch_p     = _get("branch",     1)
    row.department_s = _get("department", 0)
    row.department_p = _get("department", 1)
    row.employee_s   = _get("employee",   0)
    row.employee_p   = _get("employee",   1)
    row.industry     = industry
    row.updated_at   = datetime.utcnow()
    db.commit()
    return redirect("/settings/labels?msg=preset")