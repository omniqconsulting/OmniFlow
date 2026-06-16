"""
Phase 2 — FMS Core  (§10, §11, §12, §19.3)
Full ticket lifecycle: flow builder, stage transitions, swimlane dashboard,
reassignment, help requests, flagging, manager override, and analytics.
"""
import csv, io, json as _json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import (
    get_db, new_id,
    Tenant, User, Department, Branch,
    FMSFlow, FMSStage, FMSTicket, FMSStageHistory, FMSEvent, FMSTicketHelper,
    LibrarySubmoduleDefinition, TenantDeployedItem,
    Notification, MediaUpload,
    PMSDailyLog, DispatchRecord, InvoiceRecord,
)
from .auth import get_current_user, require_admin, require_manager
from .labels import get_labels, DEFAULT_L
from .constants import has_feature, PLAN_LIMITS
from .notifications import notify_fms_stage_transition
from .ws_manager import broadcast_sync, FMS_STAGE_TRANSITION


def _next_fms_display_id(db: Session, tenant: Tenant) -> str:
    """
    Generate the next FMS-only sequential display ID, e.g. F-0042.
    Uses MAX over existing FMS display IDs so the sequence is independent
    from the regular ticket T- counter.
    """
    max_id = db.query(func.max(FMSTicket.display_id)).filter(
        FMSTicket.tenant_id == tenant.id,
        FMSTicket.display_id.isnot(None),
    ).scalar()
    if max_id and max_id.startswith("F-"):
        try:
            next_num = int(max_id[2:]) + 1
        except ValueError:
            next_num = 1
    else:
        next_num = 1
    return f"F-{next_num:04d}"


def _check_fms_flow_limit(db: Session, tenant: Tenant) -> tuple[bool, int, object]:
    """
    Check if tenant can deploy another FMS flow given their plan.
    Returns (allowed, current_count, limit)
    """
    plan = tenant.plan or "STARTER"
    limits = PLAN_LIMITS.get(plan, {})
    max_flows = limits.get("max_fms_flows")
    current = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tenant.id,
        FMSFlow.is_deleted == False,
    ).count()
    if max_flows is None:
        return True, current, None  # unlimited
    return current < max_flows, current, max_flows

import os
BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["from_json"] = lambda s: (_json.loads(s) if s else [])


class _OrmEncoder(_json.JSONEncoder):
    """Serialize SQLAlchemy model instances as plain dicts for Jinja2 | tojson."""
    def default(self, obj):
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items()
                    if not k.startswith("_")}
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def _tojson(v) -> str:
    from markupsafe import Markup
    return Markup(_json.dumps(v, cls=_OrmEncoder))


templates.env.filters["tojson"] = _tojson

router = APIRouter(prefix="/fms", tags=["FMS"])

# ── Constants ────────────────────────────────────────────────────────────────
MANAGER_OVERRIDE_HOURS = 2   # default configurable override window (§11.3)
FMS_STATUSES = ["ACTIVE", "STAGE_COMPLETE", "IN_TRANSITION",
                "HELP_REQUESTED", "FLAGGED", "ON_HOLD", "COMPLETED", "CLOSED"]
PRIORITIES   = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _redirect(path: str):
    return RedirectResponse(path, status_code=302)

def _L(db, user):
    if user is None: return DEFAULT_L
    return get_labels(db, user.tenant_id)

def _unread(db: Session, user: User) -> int:
    return db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read == False).count()

def _ctx(request, user, db, **kw):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first() if user else None
    return {"request": request, "user": user,
            "L": _L(db, user), "unread": _unread(db, user),
            "has_inventory":  has_feature(tenant, "INVENTORY",  db) if tenant else False,
            "has_fms":        has_feature(tenant, "FMS",        db) if tenant else False,
            "has_checklists": has_feature(tenant, "CHECKLISTS", db) if tenant else False,
            **kw}

def _log(db: Session, ticket_id: str, actor_id: str, event_type: str, detail: str = ""):
    db.add(FMSEvent(ticket_id=ticket_id, actor_id=actor_id,
                    event_type=event_type, detail=detail))

def _admin_ids(db, tenant_id):
    return [u.id for u in db.query(User).filter(
        User.tenant_id == tenant_id, User.role == "ADMIN",
        User.is_deleted == False).all()]

def _manager_ids_for(db, assignee_id):
    if not assignee_id: return []
    u = db.query(User).get(assignee_id)
    return [u.manager_id] if u and u.manager_id else []

def _tat_pct(history_row: FMSStageHistory, stage: FMSStage) -> Optional[int]:
    """Return 0-100+ percentage of TaT used for the current open stage visit."""
    if not stage or not stage.target_tat_hours:
        return None
    until = history_row.exited_at or datetime.utcnow()
    elapsed_h = (until - history_row.entered_at).total_seconds() / 3600
    return int(elapsed_h / stage.target_tat_hours * 100)

def _can_transition(user: User, ticket: FMSTicket) -> bool:
    """Admins and managers can always transition; employees only their own stage."""
    if user.role in ("ADMIN", "MANAGER"):
        return True
    return ticket.current_assignee_id == user.id

def _get_ticket(db, ticket_id, tenant_id) -> FMSTicket:
    t = db.query(FMSTicket).filter(
        FMSTicket.id == ticket_id,
        FMSTicket.tenant_id == tenant_id,
        FMSTicket.is_deleted == False,
    ).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    return t

def _open_history(db, ticket_id) -> Optional[FMSStageHistory]:
    """The currently active stage history row (no exited_at)."""
    return db.query(FMSStageHistory).filter(
        FMSStageHistory.ticket_id == ticket_id,
        FMSStageHistory.exited_at == None,
    ).order_by(FMSStageHistory.entered_at.desc()).first()

def _stage_cumulative_qty(db, ticket_id, stage_id) -> int:
    result = db.query(func.sum(FMSStageHistory.qty_completed)).filter(
        FMSStageHistory.ticket_id == ticket_id,
        FMSStageHistory.stage_id  == stage_id,
    ).scalar()
    return result or 0


# ── 2-B: Flow Builder ────────────────────────────────────────────────────────

@router.get("/flows", response_class=HTMLResponse)
def fms_flows(request: Request, user: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    """2-B-1: Flow list — admin only."""
    flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.created_at).all()

    # Annotate with counts
    flow_info = []
    for f in flows:
        active_stages = [s for s in f.stages if not s.is_deleted]
        active_tickets = db.query(FMSTicket).filter(
            FMSTicket.flow_id == f.id,
            FMSTicket.is_deleted == False,
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        ).count()
        flow_info.append({"flow": f, "stage_count": len(active_stages),
                           "active_tickets": active_tickets})

    return templates.TemplateResponse(request, "fms/flow_list.html", _ctx(
        request, user, db,
        flow_info=flow_info,
    ))


@router.get("/flows/new", response_class=HTMLResponse)
def fms_flow_new(request: Request, user: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    # Flow creation is SA-only — redirect client admins
    return _redirect("/fms/flows?err=Flows+are+configured+by+your+OmniFlow+account+manager.+Contact+support+to+add+a+new+flow.")


@router.post("/flows/new")
def fms_flow_create(
    name: str = Form(...), description: str = Form(""),
    color: str = Form("#3b82f6"), is_active: bool = Form(True),
    stages_json: str = Form("[]"),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Flow creation is SA-only — block silently."""
    return _redirect("/fms/flows?err=Flow+creation+is+managed+by+your+OmniFlow+account+manager.")


@router.get("/flows/{flow_id}", response_class=HTMLResponse)
def fms_flow_edit(flow_id: str, request: Request,
                  user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    flow = _get_flow(db, flow_id, user.tenant_id)
    employees = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.is_deleted == False, User.is_active == True).all()
    deployed_submodules = _get_deployed_submodules(db, user.tenant_id)
    # Client admins get read-only view — only SA can edit flow definitions
    return templates.TemplateResponse(request, "fms/flow_edit.html", _ctx(
        request, user, db, flow=flow, employees=employees,
        mode="readonly",  # signals template to hide all edit controls
        deployed_submodules=deployed_submodules))


@router.post("/flows/{flow_id}")
def fms_flow_update(
    flow_id: str,
    name: str = Form(...), description: str = Form(""),
    color: str = Form("#3b82f6"), is_active: bool = Form(True),
    stages_json: str = Form("[]"),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Flow editing is SA-only — block silently."""
    return _redirect(f"/fms/flows/{flow_id}?err=Flow+definitions+are+managed+by+your+OmniFlow+account+manager.")


@router.post("/flows/{flow_id}/delete")
def fms_flow_delete(flow_id: str, user: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """Flow deletion is SA-only — block client admins."""
    return _redirect("/fms/flows?err=Flow+deletion+is+managed+by+your+OmniFlow+account+manager.")


@router.post("/flows/deploy-library")
def fms_deploy_library(user: User = Depends(require_admin)):
    """Flow deployment is SA-only — block client admins."""
    return _redirect("/fms/flows?err=Flows+are+deployed+by+your+OmniFlow+account+manager.+Contact+support+to+request+a+new+flow.")


@router.post("/flows/import-csv")
async def fms_flow_import(
    file: UploadFile = File(...),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """2-B-6: Bulk import flow definitions via CSV."""
    content = (await file.read()).decode("utf-8", errors="replace")
    reader  = csv.DictReader(io.StringIO(content))
    flows_created = 0
    for row in reader:
        fname = (row.get("flow_name") or "").strip()
        if not fname:
            continue
        flow = FMSFlow(
            tenant_id=user.tenant_id, name=fname,
            description=(row.get("description") or "").strip() or None,
            color=(row.get("color") or "#3b82f6").strip(),
            created_by_id=user.id,
        )
        db.add(flow)
        db.flush()
        # Parse comma-separated stage names
        stage_names = [(s.strip()) for s in (row.get("stages") or "").split("|") if s.strip()]
        for i, sname in enumerate(stage_names):
            is_terminal = (i == len(stage_names) - 1)
            db.add(FMSStage(
                flow_id=flow.id, tenant_id=user.tenant_id,
                name=sname, order=i, is_terminal=is_terminal,
            ))
        flows_created += 1
    db.commit()
    return _redirect(f"/fms/flows?imported={flows_created}")


# ── 2-C/D: Ticket Lifecycle ───────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def fms_root(user: User = Depends(get_current_user)):
    return _redirect("/fms/dashboard")


def _submodule_cols(db: Session, ticket: FMSTicket, sub_tag: Optional[str]) -> dict:
    """Return a dict of extra sub-module columns for a ticket row (P7-04)."""
    if not sub_tag:
        return {}
    tid = ticket.id
    if sub_tag == "PMS":
        logs = db.query(PMSDailyLog).filter(
            PMSDailyLog.ticket_id == tid,
            PMSDailyLog.event_type == "DAILY_LOG",
        ).order_by(PMSDailyLog.log_date.desc()).all()
        cum_qty = sum(l.qty_done for l in logs)
        pct = 0
        if ticket.target_qty and ticket.target_qty > 0:
            pct = min(int(cum_qty / ticket.target_qty * 100), 100)
        has_blockers = any(l.has_blockers for l in logs)
        last_date = logs[0].log_date if logs else None
        return {
            "target_qty": ticket.target_qty,
            "cum_qty": cum_qty,
            "pct": pct,
            "blockers": has_blockers,
            "last_entry": last_date,
        }
    if sub_tag == "DISPATCH":
        recs = db.query(DispatchRecord).filter(
            DispatchRecord.ticket_id == tid).order_by(DispatchRecord.created_at.desc()).all()
        total_disp = sum(r.qty_dispatched for r in recs)
        remaining = (ticket.target_qty or 0) - total_disp
        pod_up = any(r.proof_photo_url for r in recs)
        last_date = recs[0].created_at if recs else None
        return {
            "total_dispatched": total_disp,
            "remaining": remaining,
            "last_dispatch": last_date,
            "pod_uploaded": pod_up,
        }
    if sub_tag == "INVOICE":
        recs = db.query(InvoiceRecord).filter(
            InvoiceRecord.ticket_id == tid,
            InvoiceRecord.is_deleted == False,
        ).order_by(InvoiceRecord.due_date).all()
        total_inv = sum(r.amount for r in recs)
        total_paid = sum(r.amount for r in recs if r.is_paid)
        outstanding = total_inv - total_paid
        oldest_due = min((r.due_date for r in recs if r.due_date and not r.is_paid), default=None)
        return {
            "total_invoiced": total_inv,
            "total_received": total_paid,
            "outstanding": outstanding,
            "oldest_due": oldest_due,
        }
    return {}


@router.get("/dashboard", response_class=HTMLResponse)
def fms_dashboard(
    request: Request,
    flow_id: Optional[str] = None,
    stage_id: Optional[str] = None,   # P7-03: stage filter
    view: str = "stage_table",        # "stage_table" | "swimlane" | "list"
    dept_id: Optional[str] = None,
    manager_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    month: Optional[str] = None,      # "YYYY-MM"
    status_filter: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """FMS Dashboard — summary strip + flow cards + swimlane/list view."""
    tid = user.tenant_id
    now = datetime.utcnow()

    # All active flows for this tenant
    flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tid, FMSFlow.is_active == True,
        FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.created_at).all()

    # Select active flow (tab)
    active_flow = None
    if flow_id:
        active_flow = next((f for f in flows if f.id == flow_id), None)
    if active_flow is None and flows:
        active_flow = flows[0]

    # ── Summary strip ─────────────────────────────────────────────────────────
    base_q = db.query(FMSTicket).filter(
        FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False)

    # Role scoping — build team_ids once, reuse for all three queries
    team_ids = []
    mgr_all_fms_ids: set = set()
    if user.role == "MANAGER":
        team_ids = [u.id for u in db.query(User).filter(
            User.manager_id == user.id, User.is_deleted == False).all()]
        team_ids.append(user.id)
        # 'Ever worked on' — include historical stage assignees + helpers
        hist_tids = [h.ticket_id for h in db.query(FMSStageHistory).filter(
            FMSStageHistory.assignee_id.in_(team_ids)).distinct().all()]
        help_tids = [h.ticket_id for h in db.query(FMSTicketHelper).filter(
            FMSTicketHelper.user_id.in_(team_ids)).all()]
        mgr_all_fms_ids = set(hist_tids) | set(help_tids)
        base_q = base_q.filter(
            (FMSTicket.current_assignee_id.in_(team_ids)) |
            (FMSTicket.id.in_(mgr_all_fms_ids))
        )
    elif user.role == "EMPLOYEE":
        helper_tids = [h.ticket_id for h in db.query(FMSTicketHelper).filter(
            FMSTicketHelper.user_id == user.id).all()]
        base_q = base_q.filter(
            (FMSTicket.current_assignee_id == user.id) |
            (FMSTicket.id.in_(helper_tids))
        )

    active_tickets = base_q.filter(
        FMSTicket.status.notin_(["COMPLETED", "CLOSED"])).count()
    flagged_count  = base_q.filter(FMSTicket.is_flagged == True).count()
    awaiting_count = base_q.filter(FMSTicket.status == "ACTIVE").count()

    tat_breaches = 0
    open_tickets = base_q.filter(
        FMSTicket.status.notin_(["COMPLETED", "CLOSED"])).all()
    for t in open_tickets:
        h = _open_history(db, t.id)
        if h and t.current_stage and t.current_stage.target_tat_hours:
            elapsed = (now - h.entered_at).total_seconds() / 3600
            if elapsed > t.current_stage.target_tat_hours:
                tat_breaches += 1

    all_history = db.query(FMSStageHistory).join(
        FMSTicket, FMSStageHistory.ticket_id == FMSTicket.id
    ).join(FMSStage, FMSStageHistory.stage_id == FMSStage.id).filter(
        FMSTicket.tenant_id == tid,
        FMSStageHistory.exited_at != None,
        FMSStage.target_tat_hours != None,
    ).all()
    if all_history:
        on_time = sum(
            1 for h in all_history
            if h.stage and h.stage.target_tat_hours and
               (h.exited_at - h.entered_at).total_seconds() / 3600
               <= h.stage.target_tat_hours
        )
        compliance = int(on_time / len(all_history) * 100)
    else:
        compliance = 100

    # ── Per-flow ticket counts for flow cards ─────────────────────────────────
    flow_counts = {}
    for f in flows:
        flow_counts[f.id] = db.query(FMSTicket).filter(
            FMSTicket.flow_id == f.id, FMSTicket.is_deleted == False,
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        ).count()

    # ── Swimlane data ─────────────────────────────────────────────────────────
    tickets_by_stage: dict = {}
    tat_info: dict = {}
    if active_flow and view == "swimlane":
        active_stages = [s for s in active_flow.stages if not s.is_deleted]
        for stage in active_stages:
            tickets_by_stage[stage.id] = []

        swimlane_q = db.query(FMSTicket).filter(
            FMSTicket.flow_id == active_flow.id,
            FMSTicket.is_deleted == False,
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        )
        if user.role == "MANAGER":
            swimlane_q = swimlane_q.filter(
                (FMSTicket.current_assignee_id.in_(team_ids)) |
                (FMSTicket.id.in_(mgr_all_fms_ids))
            )
        elif user.role == "EMPLOYEE":
            swimlane_q = swimlane_q.filter(
                FMSTicket.current_assignee_id == user.id)

        for t in swimlane_q.all():
            sid = t.current_stage_id
            if sid in tickets_by_stage:
                tickets_by_stage[sid].append(t)
            h = _open_history(db, t.id)
            if h and t.current_stage and t.current_stage.target_tat_hours:
                pct = _tat_pct(h, t.current_stage)
                color = "green" if pct < 50 else "amber" if pct < 90 else "red"
            else:
                pct, color = None, "gray"
            tat_info[t.id] = {"pct": pct, "color": color,
                               "entered_at": h.entered_at if h else None}

    # ── List view data ────────────────────────────────────────────────────────
    list_tickets = []
    departments = []
    managers = []
    branches = []
    if view == "list":
        departments = db.query(Department).filter(
            Department.tenant_id == tid, Department.is_deleted == False
        ).order_by(Department.name).all()
        managers = db.query(User).filter(
            User.tenant_id == tid, User.role.in_(["MANAGER", "ADMIN"]),
            User.is_deleted == False, User.is_active == True,
        ).order_by(User.name).all()
        branches = db.query(Branch).filter(
            Branch.tenant_id == tid, Branch.is_deleted == False
        ).order_by(Branch.name).all()

        list_q = db.query(FMSTicket).filter(
            FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
        )
        # Role scoping — 'ever worked on' for Manager
        if user.role == "MANAGER":
            list_q = list_q.filter(
                (FMSTicket.current_assignee_id.in_(team_ids)) |
                (FMSTicket.id.in_(mgr_all_fms_ids))
            )
        elif user.role == "EMPLOYEE":
            helper_tids = [h.ticket_id for h in db.query(FMSTicketHelper).filter(
                FMSTicketHelper.user_id == user.id).all()]
            list_q = list_q.filter(
                (FMSTicket.current_assignee_id == user.id) |
                (FMSTicket.id.in_(helper_tids))
            )
        # Flow filter
        if flow_id and active_flow:
            list_q = list_q.filter(FMSTicket.flow_id == active_flow.id)
        # Status filter
        if status_filter == "open":
            list_q = list_q.filter(FMSTicket.status.notin_(["COMPLETED", "CLOSED"]))
        elif status_filter == "closed":
            list_q = list_q.filter(FMSTicket.status.in_(["COMPLETED", "CLOSED"]))
        elif status_filter:
            list_q = list_q.filter(FMSTicket.status == status_filter.upper())
        # Department filter: assignee's department
        if dept_id:
            dept_user_ids = [u.id for u in db.query(User).filter(
                User.department_id == dept_id, User.tenant_id == tid,
                User.is_deleted == False).all()]
            list_q = list_q.filter(FMSTicket.current_assignee_id.in_(dept_user_ids))
        # Manager filter: assignee's manager
        if manager_id:
            mgr_team_ids = [u.id for u in db.query(User).filter(
                User.manager_id == manager_id, User.tenant_id == tid,
                User.is_deleted == False).all()]
            mgr_team_ids.append(manager_id)
            list_q = list_q.filter(FMSTicket.current_assignee_id.in_(mgr_team_ids))
        # Branch filter: assignee's branch (via department)
        if branch_id:
            branch_dept_ids = [d.id for d in db.query(Department).filter(
                Department.branch_id == branch_id, Department.tenant_id == tid,
                Department.is_deleted == False).all()]
            branch_user_ids = [u.id for u in db.query(User).filter(
                User.department_id.in_(branch_dept_ids), User.tenant_id == tid,
                User.is_deleted == False).all()]
            list_q = list_q.filter(FMSTicket.current_assignee_id.in_(branch_user_ids))
        # Month filter: created_at
        if month:
            try:
                y, m = int(month[:4]), int(month[5:7])
                month_start = datetime(y, m, 1)
                month_end = datetime(y + (m // 12), (m % 12) + 1, 1)
                list_q = list_q.filter(
                    FMSTicket.created_at >= month_start,
                    FMSTicket.created_at < month_end,
                )
            except (ValueError, IndexError):
                pass

        list_q = list_q.order_by(FMSTicket.created_at.desc())
        raw_tickets = list_q.limit(200).all()

        for t in raw_tickets:
            h = _open_history(db, t.id)
            days_open = (now - t.created_at).days if t.created_at else 0
            if h and t.current_stage and t.current_stage.target_tat_hours:
                pct = _tat_pct(h, t.current_stage)
                tat_color = "green" if pct < 50 else "amber" if pct < 90 else "red"
            else:
                pct, tat_color = None, "gray"
            # Determine previous stage via history (most recent exited row)
            prev_h = db.query(FMSStageHistory).filter(
                FMSStageHistory.ticket_id == t.id,
                FMSStageHistory.exited_at != None,
            ).order_by(FMSStageHistory.exited_at.desc()).first()
            prev_stage = prev_h.stage if prev_h else None
            list_tickets.append({
                "ticket": t,
                "days_open": days_open,
                "stage_name": t.current_stage.name if t.current_stage else "—",
                "flow_name": t.flow.name if t.flow else "—",
                "flow_color": t.flow.color if t.flow else "#64748b",
                "assignee_name": t.current_assignee.name if t.current_assignee else "—",
                "tat_pct": pct,
                "tat_color": tat_color,
                "stage_entered_at": h.entered_at if h else None,
                "prev_stage_id": prev_stage.id if prev_stage else None,
                "prev_stage_name": prev_stage.name if prev_stage else None,
            })

    # Flagged tickets for escalations panel (admin/manager)
    flagged_tickets = []
    if user.role in ("ADMIN", "MANAGER"):
        flagged_tickets = base_q.filter(
            FMSTicket.is_flagged == True,
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        ).limit(10).all()

    can_drag = user.role in ("ADMIN", "MANAGER")

    employees = db.query(User).filter(
        User.tenant_id == tid, User.is_deleted == False, User.is_active == True,
    ).order_by(User.name).all()

    # ── P7-03/04: Stage-table view ────────────────────────────────────────────
    stage_table_stages = []
    active_stage = None
    stage_tickets = []
    stage_ticket_counts: dict = {}

    if active_flow:
        stage_table_stages = sorted(
            [s for s in active_flow.stages if not s.is_deleted], key=lambda s: s.order
        )
        # Per-stage ticket counts for badges
        for s in stage_table_stages:
            stage_ticket_counts[s.id] = db.query(FMSTicket).filter(
                FMSTicket.current_stage_id == s.id,
                FMSTicket.is_deleted == False,
                FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
            ).count()

        # Determine active stage (default: first)
        if stage_id:
            active_stage = next((s for s in stage_table_stages if s.id == stage_id), None)
        if active_stage is None and stage_table_stages:
            active_stage = stage_table_stages[0]

        if active_stage and view == "stage_table":
            q = db.query(FMSTicket).filter(
                FMSTicket.current_stage_id == active_stage.id,
                FMSTicket.is_deleted == False,
                FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
            )
            if user.role == "MANAGER":
                q = q.filter(
                    (FMSTicket.current_assignee_id.in_(team_ids)) |
                    (FMSTicket.id.in_(mgr_all_fms_ids))
                )
            elif user.role == "EMPLOYEE":
                q = q.filter(FMSTicket.current_assignee_id == user.id)

            raw = q.order_by(FMSTicket.created_at.desc()).all()
            for t in raw:
                h = _open_history(db, t.id)
                if h and active_stage.target_tat_hours:
                    pct = _tat_pct(h, active_stage)
                    tc = "green" if pct < 50 else "amber" if pct < 90 else "red"
                else:
                    pct, tc = None, "gray"
                sub_cols = _submodule_cols(db, t, active_stage.sub_module_tag)
                stage_tickets.append({
                    "ticket": t,
                    "tat_pct": pct,
                    "tat_color": tc,
                    "assignee_name": t.current_assignee.name if t.current_assignee else "—",
                    "sub": sub_cols,
                    "entered_at": h.entered_at if h else None,
                })

    # Next stage for each active_stage (used by Mark Stage Complete modal)
    next_stage_map: dict = {}
    for i, s in enumerate(stage_table_stages):
        if i + 1 < len(stage_table_stages):
            next_stage_map[s.id] = stage_table_stages[i + 1]

    return templates.TemplateResponse(request, "fms/dashboard.html", _ctx(
        request, user, db,
        flows=flows, active_flow=active_flow,
        flow_counts=flow_counts,
        view=view,
        # P7-03/04: stage-table view
        stage_table_stages=stage_table_stages,
        active_stage=active_stage,
        stage_tickets=stage_tickets,
        stage_ticket_counts=stage_ticket_counts,
        next_stage_map=next_stage_map,
        # swimlane
        tickets_by_stage=tickets_by_stage,
        tat_info=tat_info,
        flagged_tickets=flagged_tickets,
        can_drag=can_drag,
        # list view
        list_tickets=list_tickets,
        departments=departments,
        managers=managers,
        branches=branches,
        # active filters
        f_dept_id=dept_id or "",
        f_manager_id=manager_id or "",
        f_branch_id=branch_id or "",
        f_month=month or "",
        f_status=status_filter or "",
        employees=employees,
        # summary strip
        active_tickets=active_tickets,
        tat_breaches=tat_breaches,
        flagged_count=flagged_count,
        awaiting_count=awaiting_count,
        compliance=compliance,
        now=now,
    ))


@router.get("/tickets/new", response_class=HTMLResponse)
def fms_ticket_new(
    request: Request, flow_id: Optional[str] = None,
    user: User = Depends(require_manager), db: Session = Depends(get_db),
):
    """2-C-1: Ticket creation form."""
    flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id, FMSFlow.is_active == True,
        FMSFlow.is_deleted == False).all()
    employees = db.query(User).filter(
        User.tenant_id == user.tenant_id, User.is_deleted == False,
        User.is_active == True).all()
    selected_flow = None
    if flow_id:
        selected_flow = next((f for f in flows if f.id == flow_id), None)
    from .linked_entities import get_linked_entity_options as _geo
    entity_options = _geo(db, user.tenant_id)
    return templates.TemplateResponse(request, "fms/ticket_new.html", _ctx(
        request, user, db,
        flows=flows, employees=employees,
        selected_flow=selected_flow,
        priorities=PRIORITIES,
        entity_options=entity_options,
    ))


@router.post("/tickets/new")
async def fms_ticket_create(
    request: Request,
    title: str = Form(...), description: str = Form(""),
    flow_id: str = Form(...), starting_stage_id: str = Form(...),
    priority: str = Form("MEDIUM"), assignee_id: str = Form(...),
    wo_number: str = Form(""), due_at: str = Form(""),
    target_qty: str = Form(""), qty_unit: str = Form(""),
    evidence_required: bool = Form(False),
    user: User = Depends(require_manager), db: Session = Depends(get_db),
):
    """2-C-1 / P7-06: Create FMS ticket with evidence_required + linked entities."""
    flow = _get_flow(db, flow_id, user.tenant_id)
    stage = db.query(FMSStage).filter(
        FMSStage.id == starting_stage_id,
        FMSStage.flow_id == flow_id).first()
    if not stage:
        raise HTTPException(400, "Invalid starting stage")

    ticket = FMSTicket(
        tenant_id=user.tenant_id, flow_id=flow_id,
        current_stage_id=stage.id, title=title,
        description=description or None,
        wo_number=wo_number or None, priority=priority,
        target_qty=int(target_qty) if target_qty.strip() else None,
        qty_unit=qty_unit or None,
        current_assignee_id=assignee_id,
        due_at=datetime.fromisoformat(due_at) if due_at.strip() else None,
        created_by_id=user.id, status="ACTIVE",
    )
    db.add(ticket)
    db.flush()

    tenant = db.query(Tenant).get(user.tenant_id)
    ticket.display_id = _next_fms_display_id(db, tenant)

    db.add(FMSStageHistory(
        ticket_id=ticket.id, stage_id=stage.id,
        stage_name=stage.name, assignee_id=assignee_id,
        direction="FORWARD",
    ))
    _log(db, ticket.id, user.id, "CREATED", title)
    _log(db, ticket.id, user.id, "STAGE_ENTERED", stage.name)
    db.commit()
    db.refresh(ticket)

    # P7-06: save linked entities
    from .linked_entities import save_linked_entities_from_form as _slf
    form_data = dict(await request.form())
    _slf(db, form_data, "FMS_TICKET", ticket.id, user.tenant_id, user.id)

    admins   = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for(db, assignee_id)
    notify_fms_stage_transition(
        user.tenant_id, ticket.id, ticket.title,
        stage.name, user.id, admins, managers, assignee_id)

    return _redirect(f"/fms/tickets/{ticket.id}")


# ── P7-08: Edit FMS Ticket ────────────────────────────────────────────────────

@router.post("/tickets/{ticket_id}/edit")
def fms_ticket_edit(
    ticket_id: str,
    title: str = Form(...), description: str = Form(""),
    priority: str = Form("MEDIUM"),
    due_at: str = Form(""), wo_number: str = Form(""),
    user: User = Depends(require_manager), db: Session = Depends(get_db),
):
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    if ticket.status in ("COMPLETED", "CLOSED"):
        raise HTTPException(400, "Cannot edit a completed or closed ticket")
    old = f"title={ticket.title}, priority={ticket.priority}"
    ticket.title = title
    ticket.description = description or None
    ticket.priority = priority
    ticket.wo_number = wo_number or None
    ticket.due_at = datetime.fromisoformat(due_at) if due_at.strip() else ticket.due_at
    ticket.updated_at = datetime.utcnow()
    _log(db, ticket_id, user.id, "EDITED", f"Was: {old}")
    db.commit()
    return _redirect(f"/fms/tickets/{ticket_id}")


# ── P7-08: Delete FMS Ticket ──────────────────────────────────────────────────

@router.post("/tickets/{ticket_id}/delete")
def fms_ticket_delete(ticket_id: str,
                       user: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    # Only allowed if no stage history entries (created in error)
    history_count = db.query(FMSStageHistory).filter(
        FMSStageHistory.ticket_id == ticket_id,
        FMSStageHistory.exited_at != None,
    ).count()
    if history_count > 0:
        raise HTTPException(400, "Cannot delete a ticket that has stage history")
    ticket.is_deleted = True
    _log(db, ticket_id, user.id, "DELETED", "Soft deleted by admin")
    db.commit()
    return _redirect("/fms/dashboard")


# ── P7-07: Bulk upload FMS tickets ────────────────────────────────────────────

@router.get("/tickets/bulk-template")
def fms_bulk_template(user: User = Depends(require_manager)):
    import csv as _csv, io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["title","description","flow_name","priority","assignee_phone",
                "due_at","target_qty","qty_unit","wo_number","evidence_required"])
    w.writerow(["Mandatory. Max 200 chars","Mandatory. Work description",
                "Exact active flow name","LOW|MEDIUM|HIGH|CRITICAL",
                "Active user phone number","YYYY-MM-DD HH:MM (24h)",
                "Integer (opt)","pcs/kg/m etc (opt)","WO ref (opt)","TRUE|FALSE (default FALSE)"])
    w.writerow(["Steel Frame Batch","Cut and weld 100 frames","Manufacturing Flow",
                "MEDIUM","+911234567890","2026-07-20 18:00","100","pcs","WO-001","FALSE"])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read().encode()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fms_tickets_template.csv"},
    )


@router.post("/tickets/bulk-upload")
async def fms_bulk_upload(
    file: UploadFile = File(...),
    user: User = Depends(require_manager), db: Session = Depends(get_db),
):
    import csv as _csv, io as _io
    content = (await file.read()).decode("utf-8-sig")
    reader = _csv.DictReader(_io.StringIO(content))
    errors = []
    created = 0
    for i, row in enumerate(reader, start=1):
        if i == 2:
            continue  # skip description row
        title = (row.get("title") or "").strip()
        desc  = (row.get("description") or "").strip()
        flow_name = (row.get("flow_name") or "").strip()
        priority = (row.get("priority") or "MEDIUM").strip().upper()
        phone = (row.get("assignee_phone") or "").strip()
        due_str = (row.get("due_at") or "").strip()

        if not title or not desc or not flow_name or not phone or not due_str:
            errors.append((i, title or "(blank)", "title, description, flow_name, assignee_phone, due_at are required"))
            continue
        if priority not in ("LOW","MEDIUM","HIGH","CRITICAL"):
            errors.append((i, title, f"Invalid priority: {priority}")); continue

        flow = db.query(FMSFlow).filter(
            FMSFlow.tenant_id == user.tenant_id,
            FMSFlow.name == flow_name, FMSFlow.is_active == True,
            FMSFlow.is_deleted == False).first()
        if not flow:
            errors.append((i, title, f"Flow not found: {flow_name}")); continue

        assignee = db.query(User).filter(
            User.tenant_id == user.tenant_id, User.phone == phone,
            User.is_deleted == False).first()
        if not assignee:
            errors.append((i, title, f"User not found with phone: {phone}")); continue

        first_stage = db.query(FMSStage).filter(
            FMSStage.flow_id == flow.id, FMSStage.is_deleted == False,
        ).order_by(FMSStage.order).first()
        if not first_stage:
            errors.append((i, title, f"Flow has no stages: {flow_name}")); continue

        try:
            due = datetime.strptime(due_str, "%Y-%m-%d %H:%M")
        except ValueError:
            errors.append((i, title, f"Invalid due_at format: {due_str}")); continue

        tq = (row.get("target_qty") or "").strip()
        ev_req = (row.get("evidence_required") or "FALSE").strip().upper() == "TRUE"

        ticket = FMSTicket(
            tenant_id=user.tenant_id, flow_id=flow.id,
            current_stage_id=first_stage.id, title=title,
            description=desc, priority=priority,
            wo_number=(row.get("wo_number") or "").strip() or None,
            target_qty=int(tq) if tq.isdigit() else None,
            qty_unit=(row.get("qty_unit") or "").strip() or None,
            current_assignee_id=assignee.id, due_at=due,
            created_by_id=user.id, status="ACTIVE",
        )
        db.add(ticket)
        db.flush()
        tenant = db.query(Tenant).get(user.tenant_id)
        ticket.display_id = _next_fms_display_id(db, tenant)
        db.add(FMSStageHistory(
            ticket_id=ticket.id, stage_id=first_stage.id,
            stage_name=first_stage.name, assignee_id=assignee.id,
            direction="FORWARD",
        ))
        _log(db, ticket.id, user.id, "CREATED", f"Bulk import: {title}")
        created += 1

    db.commit()
    if errors:
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["row","title","error"])
        for (r, t, e) in errors:
            w.writerow([r, t, e])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.read().encode()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=fms_upload_errors.csv"},
        )
    return _redirect(f"/fms/dashboard?uploaded={created}")


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def fms_ticket_detail(
    ticket_id: str, request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    flow   = ticket.flow
    now    = datetime.utcnow()
    stages = sorted(
        [s for s in flow.stages if not s.is_deleted], key=lambda s: s.order
    ) if flow else []

    # All history rows for this ticket, oldest first
    all_histories = db.query(FMSStageHistory).filter(
        FMSStageHistory.ticket_id == ticket_id,
    ).order_by(FMSStageHistory.entered_at).all()

    # All events for this ticket, oldest first
    all_events = db.query(FMSEvent).filter(
        FMSEvent.ticket_id == ticket_id,
    ).order_by(FMSEvent.created_at).all()

    # Current open history row
    open_h  = next((h for h in all_histories if h.exited_at is None), None)
    tat_pct = _tat_pct(open_h, ticket.current_stage) if open_h and ticket.current_stage else None

    cur_order = ticket.current_stage.order if ticket.current_stage else -1

    # ── Build per-stage accordion data ───────────────────────────────────────
    def _events_in_window(entered_at, exited_at):
        """Events whose created_at falls within a history row's time window."""
        result = []
        for ev in all_events:
            if ev.created_at >= entered_at:
                if exited_at is None or ev.created_at <= exited_at:
                    result.append(ev)
        return result

    stage_panels = []
    for s in stages:
        is_current = (s.id == ticket.current_stage_id)
        is_done    = (s.order < cur_order)
        is_future  = not is_current and not is_done

        visits = [h for h in all_histories if h.stage_id == s.id]

        # Total time spent across all completed visits
        total_secs = sum(
            (h.exited_at - h.entered_at).total_seconds()
            for h in visits if h.exited_at
        )
        # Time on current open visit (if this is the active stage)
        current_secs = None
        if is_current and open_h and open_h.stage_id == s.id:
            current_secs = (now - open_h.entered_at).total_seconds()

        # TaT status for this stage
        if is_current and tat_pct is not None:
            s_tat_pct   = tat_pct
            s_tat_color = "green" if tat_pct < 50 else "amber" if tat_pct < 90 else "red"
        else:
            s_tat_pct, s_tat_color = None, "gray"

        # Collect events per visit window
        enriched_visits = []
        for h in visits:
            win_events = _events_in_window(h.entered_at, h.exited_at)
            enriched_visits.append({
                "history": h,
                "events":  win_events,
                "duration_h": round(
                    (h.exited_at - h.entered_at).total_seconds() / 3600, 1
                ) if h.exited_at else None,
                "is_open": h.exited_at is None,
            })

        # Unique assignees seen at this stage
        assignee_ids = list(dict.fromkeys(
            h.assignee_id for h in visits if h.assignee_id
        ))
        assignee_names = []
        for aid in assignee_ids:
            u = db.query(User).get(aid)
            if u:
                assignee_names.append(u.name)

        stage_panels.append({
            "stage":          s,
            "is_current":     is_current,
            "is_done":        is_done,
            "is_future":      is_future,
            "total_visits":   len(visits),
            "total_hours":    round(total_secs / 3600, 1),
            "current_secs":   current_secs,
            "tat_pct":        s_tat_pct,
            "tat_color":      s_tat_color,
            "enriched_visits": enriched_visits,
            "assignee_names": assignee_names,
            "qty_done":       _stage_cumulative_qty(db, ticket_id, s.id),
            "sub_module_tag": s.sub_module_tag,
        })

    # Manager override window still open?
    can_override = False
    if user.role in ("ADMIN", "MANAGER"):
        last_exit = next(
            (h for h in reversed(all_histories) if h.exited_at), None
        )
        if last_exit and last_exit.exited_at:
            age_h = (now - last_exit.exited_at).total_seconds() / 3600
            can_override = age_h <= MANAGER_OVERRIDE_HOURS

    employees = db.query(User).filter(
        User.tenant_id == user.tenant_id, User.is_deleted == False,
        User.is_active == True).all()
    helper_ids = [h.user_id for h in ticket.helpers]

    can_transition = _can_transition(user, ticket)
    can_manage     = user.role in ("ADMIN", "MANAGER")

    from .linked_entities import get_linked_entity_options as _geo
    entity_options = _geo(db, user.tenant_id)

    return templates.TemplateResponse(request, "fms/ticket_detail.html", _ctx(
        request, user, db,
        ticket=ticket, flow=flow, stages=stages,
        stage_panels=stage_panels,
        open_h=open_h, tat_pct=tat_pct,
        can_override=can_override,
        employees=employees, helper_ids=helper_ids,
        can_transition=can_transition, can_manage=can_manage,
        now=now,
        entity_options=entity_options,
    ))


@router.post("/tickets/{ticket_id}/transition")
async def fms_transition(
    ticket_id: str,
    next_stage_id: str = Form(...),
    new_assignee_id: str = Form(...),
    completion_note: str = Form(""),
    qty_completed: str = Form("0"),
    return_reason: str = Form(""),
    is_override: bool = Form(False),
    evidence_file: UploadFile = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    2-C-2/3/4/5/6/7: Stage transition engine.
    Handles FORWARD, BACKWARD, non-linear revisits, and manager override.
    """
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    if not _can_transition(user, ticket):
        raise HTTPException(403, "Not authorised to transition this ticket")
    if ticket.status in ("COMPLETED", "CLOSED"):
        raise HTTPException(400, "Ticket is already completed or closed")

    next_stage = db.query(FMSStage).filter(
        FMSStage.id == next_stage_id,
        FMSStage.flow_id == ticket.flow_id).first()
    if not next_stage:
        raise HTTPException(400, "Invalid next stage")

    cur_stage  = ticket.current_stage
    open_h     = _open_history(db, ticket_id)

    # Determine direction (2-C-3/4)
    cur_order  = cur_stage.order  if cur_stage  else 0
    next_order = next_stage.order
    direction  = "BACKWARD" if next_order < cur_order else "FORWARD"

    if is_override:
        # 2-C-7: manager override
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Only managers/admins can override")
        direction = "MANAGER_OVERRIDE"

    # 2-C-4: backward requires reason
    if direction == "BACKWARD" and not return_reason.strip():
        raise HTTPException(400, "Return reason is required for backward movement")

    # Stage requires completion note
    if cur_stage and cur_stage.completion_note_required and not completion_note.strip():
        raise HTTPException(400, f"Stage '{cur_stage.name}' requires a completion note")

    # Stage requires evidence upload
    evidence_url = None
    evidence_filename = None
    if cur_stage and getattr(cur_stage, "evidence_required", False):
        has_file = (evidence_file is not None
                    and evidence_file.filename
                    and evidence_file.filename.strip())
        if not has_file:
            raise HTTPException(
                400,
                f"Stage '{cur_stage.name}' requires an evidence file upload before moving on"
            )
        from .uploads import save_upload as _save_upload
        result = await _save_upload(evidence_file, user.tenant_id)
        evidence_url = result["file_path"]
        evidence_filename = result["file_name"]

    qty = int(qty_completed) if qty_completed.strip().isdigit() else 0

    # Close current stage history row
    if open_h:
        open_h.exited_at        = datetime.utcnow()
        open_h.completion_note  = completion_note.strip() or None
        open_h.qty_completed    = qty
        open_h.evidence_url     = evidence_url
        open_h.evidence_filename= evidence_filename
        _log(db, ticket_id, user.id, "STAGE_EXITED",
             f"From: {cur_stage.name if cur_stage else '?'} | "
             f"note: {completion_note[:80]}" if completion_note else "")

    # Create new stage history row (2-C-5: non-linear — always new row)
    db.add(FMSStageHistory(
        ticket_id=ticket_id, stage_id=next_stage_id,
        stage_name=next_stage.name, assignee_id=new_assignee_id,
        direction=direction,
        return_reason=return_reason.strip() or None,
        from_stage_id=cur_stage.id if cur_stage else None,
        from_stage_name=cur_stage.name if cur_stage else None,
    ))

    # Update ticket
    ticket.current_stage_id    = next_stage_id
    ticket.current_assignee_id = new_assignee_id
    ticket.updated_at          = datetime.utcnow()

    if next_stage.is_terminal:
        ticket.status       = "COMPLETED"
        ticket.completed_at = datetime.utcnow()
        _log(db, ticket_id, user.id, "COMPLETED",
             f"Reached terminal stage: {next_stage.name}")
    else:
        ticket.status = "ACTIVE"

    event_type = "RETURNED" if direction == "BACKWARD" else (
        "MANAGER_OVERRIDE" if direction == "MANAGER_OVERRIDE" else "STAGE_ENTERED")
    detail_parts = [f"To: {next_stage.name}"]
    if return_reason: detail_parts.append(f"Reason: {return_reason}")
    _log(db, ticket_id, user.id, event_type, " | ".join(detail_parts))

    db.commit()

    # WS broadcast
    admins   = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for(db, new_assignee_id)
    notify_fms_stage_transition(
        user.tenant_id, ticket_id, ticket.title,
        next_stage.name, user.id, admins, managers, new_assignee_id)
    audience = list(set(admins + managers + [new_assignee_id]))
    broadcast_sync(user.tenant_id, audience, FMS_STAGE_TRANSITION, {
        "ticket_id": ticket_id, "display_id": ticket.display_id,
        "title": ticket.title, "stage": next_stage.name,
        "status": ticket.status,
    })

    return _redirect(f"/fms/tickets/{ticket_id}")


@router.post("/tickets/bulk-action")
async def fms_bulk_action(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk send-back or bulk close for FMS tickets from list view."""
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(403)
    form = await request.form()
    action = form.get("action", "")
    ids = form.getlist("ticket_ids")
    if not ids or action not in ("send_back", "close"):
        return _redirect("/fms/dashboard?view=list")

    tid = user.tenant_id
    tickets = db.query(FMSTicket).filter(
        FMSTicket.id.in_(ids), FMSTicket.tenant_id == tid,
        FMSTicket.is_deleted == False).all()

    for t in tickets:
        if action == "close":
            if t.status not in ("COMPLETED", "CLOSED"):
                t.status = "CLOSED"
                t.updated_at = datetime.utcnow()
                _log(db, t.id, user.id, "CLOSED", "Bulk closed from list view")
        elif action == "send_back":
            # Find the last exited stage and send back to it
            prev_h = db.query(FMSStageHistory).filter(
                FMSStageHistory.ticket_id == t.id,
                FMSStageHistory.exited_at != None,
            ).order_by(FMSStageHistory.exited_at.desc()).first()
            if prev_h and prev_h.stage_id and t.status not in ("COMPLETED", "CLOSED"):
                # Close current open history row
                open_h = _open_history(db, t.id)
                if open_h:
                    open_h.exited_at = datetime.utcnow()
                # Open new history row for prev stage
                db.add(FMSStageHistory(
                    id=new_id(), ticket_id=t.id,
                    stage_id=prev_h.stage_id,
                    assignee_id=t.current_assignee_id,
                    entered_at=datetime.utcnow(),
                    direction="BACKWARD",
                    return_reason="Bulk send-back from list view",
                ))
                t.current_stage_id = prev_h.stage_id
                t.status = "ACTIVE"
                t.updated_at = datetime.utcnow()
                _log(db, t.id, user.id, "RETURNED",
                     f"Bulk send-back to {prev_h.stage.name if prev_h.stage else '?'}")

    db.commit()
    return _redirect("/fms/dashboard?view=list&advanced=1")


@router.post("/tickets/{ticket_id}/action")
def fms_action(
    ticket_id: str,
    action: str = Form(...),
    comment: str = Form(""),
    reason: str = Form(""),
    new_assignee_id: str = Form(""),
    helper_id: str = Form(""),
    flag_reason: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """2-D: Reassign, help request, flag, comment, on-hold, close."""
    ticket = _get_ticket(db, ticket_id, user.tenant_id)

    if action == "comment" and comment.strip():
        _log(db, ticket_id, user.id, "COMMENT", comment.strip())

    elif action == "reassign" and new_assignee_id and reason.strip():
        # 2-D-1/2: reassign — mandatory handoff form
        if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
            raise HTTPException(403, "Only the current assignee can reassign")
        old_assignee = ticket.current_assignee_id
        ticket.current_assignee_id = new_assignee_id
        ticket.updated_at = datetime.utcnow()
        # Update open stage history assignee
        open_h = _open_history(db, ticket_id)
        if open_h:
            open_h.assignee_id = new_assignee_id
        _log(db, ticket_id, user.id, "REASSIGNED",
             f"From: {old_assignee} → To: {new_assignee_id} | {reason}")

    elif action == "help_request" and comment.strip():
        # 2-D-3
        ticket.status = "HELP_REQUESTED"
        _log(db, ticket_id, user.id, "HELP_REQUESTED", comment.strip())

    elif action == "add_helper" and helper_id:
        # 2-D-3
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        existing = db.query(FMSTicketHelper).filter(
            FMSTicketHelper.ticket_id == ticket_id,
            FMSTicketHelper.user_id == helper_id).first()
        if not existing:
            db.add(FMSTicketHelper(
                ticket_id=ticket_id, user_id=helper_id,
                added_by_id=user.id, reason=reason.strip() or None))
            _log(db, ticket_id, user.id, "HELPER_ADDED",
                 f"Helper: {helper_id} | {reason}")

    elif action == "remove_helper" and helper_id:
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        db.query(FMSTicketHelper).filter(
            FMSTicketHelper.ticket_id == ticket_id,
            FMSTicketHelper.user_id == helper_id).delete()
        _log(db, ticket_id, user.id, "HELPER_REMOVED", f"Helper: {helper_id}")

    elif action == "flag" and flag_reason.strip():
        # 2-D-4
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        ticket.is_flagged    = True
        ticket.flagged_reason = flag_reason.strip()
        _log(db, ticket_id, user.id, "FLAGGED", flag_reason.strip())

    elif action == "unflag":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        ticket.is_flagged     = False
        ticket.flagged_reason = None
        _log(db, ticket_id, user.id, "UNFLAGGED")

    elif action == "on_hold":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        ticket.status = "ON_HOLD"
        _log(db, ticket_id, user.id, "ON_HOLD", reason)

    elif action == "resume":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        ticket.status = "ACTIVE"
        _log(db, ticket_id, user.id, "RESUMED")

    elif action == "close":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        ticket.status    = "CLOSED"
        ticket.closed_at = datetime.utcnow()
        _log(db, ticket_id, user.id, "CLOSED", reason)

    elif action == "mark_stage_complete":
        if not _can_transition(user, ticket):
            raise HTTPException(403)
        ticket.status = "STAGE_COMPLETE"
        _log(db, ticket_id, user.id, "STAGE_EXITED",
             f"Marked complete at {ticket.current_stage.name if ticket.current_stage else '?'}")

    ticket.updated_at = datetime.utcnow()
    db.commit()
    return _redirect(f"/fms/tickets/{ticket_id}")


# ── 2-F: FMS Analytics ───────────────────────────────────────────────────────

@router.get("/analytics", response_class=HTMLResponse)
def fms_analytics(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """2-F-1/2: TaT breach rates and stage compliance per employee."""
    tid    = user.tenant_id
    tenant = db.query(Tenant).get(tid)
    is_pro = has_feature(tenant, "KPI_CHARTS_ADMIN", db)

    # 2-F-2: My own compliance (all roles)
    my_history = db.query(FMSStageHistory).join(
        FMSTicket, FMSStageHistory.ticket_id == FMSTicket.id
    ).join(FMSStage, FMSStageHistory.stage_id == FMSStage.id).filter(
        FMSTicket.tenant_id == tid,
        FMSStageHistory.assignee_id == user.id,
        FMSStageHistory.exited_at != None,
        FMSStage.target_tat_hours != None,
    ).all()
    my_total  = len(my_history)
    my_ontime = sum(
        1 for h in my_history
        if h.stage and h.stage.target_tat_hours and
           (h.exited_at - h.entered_at).total_seconds() / 3600 <= h.stage.target_tat_hours
    )
    my_compliance = int(my_ontime / my_total * 100) if my_total else 100

    # 2-F-1: Per-employee TaT analysis (Professional+ admin/manager only)
    emp_analytics = []
    if is_pro and user.role in ("ADMIN", "MANAGER"):
        employees = db.query(User).filter(
            User.tenant_id == tid, User.is_deleted == False).all()
        for emp in employees:
            emp_hist = db.query(FMSStageHistory).join(
                FMSTicket, FMSStageHistory.ticket_id == FMSTicket.id
            ).join(FMSStage, FMSStageHistory.stage_id == FMSStage.id).filter(
                FMSTicket.tenant_id == tid,
                FMSStageHistory.assignee_id == emp.id,
                FMSStageHistory.exited_at != None,
                FMSStage.target_tat_hours != None,
            ).all()
            if not emp_hist:
                continue
            breaches  = sum(
                1 for h in emp_hist
                if h.stage and h.stage.target_tat_hours and
                   (h.exited_at - h.entered_at).total_seconds() / 3600
                   > h.stage.target_tat_hours
            )
            ontime_pct = int((1 - breaches / len(emp_hist)) * 100) if emp_hist else 100
            emp_analytics.append({
                "user": emp,
                "total_stages": len(emp_hist),
                "breaches": breaches,
                "ontime_pct": ontime_pct,
            })
        emp_analytics.sort(key=lambda x: x["ontime_pct"])

    # Per-flow breach counts (for summary)
    flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tid, FMSFlow.is_deleted == False).all()
    flow_breaches = {}
    for f in flows:
        fh = db.query(FMSStageHistory).join(
            FMSTicket, FMSStageHistory.ticket_id == FMSTicket.id
        ).join(FMSStage, FMSStageHistory.stage_id == FMSStage.id).filter(
            FMSTicket.flow_id == f.id,
            FMSStageHistory.exited_at != None,
            FMSStage.target_tat_hours != None,
        ).all()
        flow_breaches[f.id] = sum(
            1 for h in fh
            if h.stage and h.stage.target_tat_hours and
               (h.exited_at - h.entered_at).total_seconds() / 3600
               > h.stage.target_tat_hours
        )

    return templates.TemplateResponse(request, "fms/analytics.html", _ctx(
        request, user, db,
        my_total=my_total, my_ontime=my_ontime, my_compliance=my_compliance,
        emp_analytics=emp_analytics, is_pro=is_pro,
        flows=flows, flow_breaches=flow_breaches,
    ))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_deployed_submodules(db: Session, tenant_id: str) -> list:
    """Return list of LibrarySubmoduleDefinition deployed to this tenant.
    Used by the flow editor to populate the sub-module dropdown."""
    deployed_ids = [
        row.library_item_id for row in
        db.query(TenantDeployedItem).filter(
            TenantDeployedItem.tenant_id == tenant_id,
            TenantDeployedItem.item_type == "submodule",
        ).all()
    ]
    if not deployed_ids:
        return []
    return db.query(LibrarySubmoduleDefinition).filter(
        LibrarySubmoduleDefinition.id.in_(deployed_ids),
        LibrarySubmoduleDefinition.status == "PUBLISHED",
    ).order_by(
        LibrarySubmoduleDefinition.sub_module_type,
        LibrarySubmoduleDefinition.name,
    ).all()


def _get_flow(db: Session, flow_id: str, tenant_id: str) -> FMSFlow:
    f = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id, FMSFlow.tenant_id == tenant_id,
        FMSFlow.is_deleted == False).first()
    if not f:
        raise HTTPException(404, "Flow not found")
    return f


def _save_stages(db: Session, flow_id: str, tenant_id: str, stages_json: str):
    """Parse stages JSON from the stage editor and insert FMSStage rows."""
    try:
        stages = _json.loads(stages_json) if stages_json else []
    except Exception:
        stages = []
    for i, s in enumerate(stages):
        name = (s.get("name") or "").strip()
        if not name:
            continue
        smt = (s.get("sub_module_tag") or "").strip().upper() or None
        db.add(FMSStage(
            flow_id=flow_id, tenant_id=tenant_id,
            name=name, order=s.get("order", i),
            color=s.get("color", "#3b82f6"),
            target_tat_hours=s.get("target_tat_hours") or None,
            default_assignee_id=s.get("default_assignee_id") or None,
            sub_module_tag=smt,
            is_mandatory=bool(s.get("is_mandatory", True)),
            completion_note_required=bool(s.get("completion_note_required", False)),
            is_terminal=bool(s.get("is_terminal", False)),
        ))
