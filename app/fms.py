"""
Phase 2 — FMS Core  (§10, §11, §12, §19.3)
Full ticket lifecycle: flow builder, stage transitions, swimlane dashboard,
reassignment, help requests, flagging, manager override, and analytics.
"""
import csv, io, json as _json
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile, File
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
    Customer, Vendor, RawMaterial,
    CustomReferenceList, CustomReferenceItem,
)
from .auth import get_current_user, require_admin, require_manager
from .labels import get_labels, DEFAULT_L
from .constants import has_feature, PLAN_LIMITS, BULK_IMPORT_MAX_ROWS
from .notifications import (
    create_notification,
    notify_fms_stage_transition,
    send_whatsapp_for_fms_stage_transition,
    send_whatsapp_for_fms_ticket_created,
    notify_fms_ticket_opened,
)
from .ws_manager import broadcast_sync, FMS_STAGE_TRANSITION
import json as _json_module


def _build_ref_lists_json(tenant_id: str, db) -> str:
    """Combined system entity tables + custom reference lists for field-builder dropdowns."""
    result = []
    _sys = [
        ("__system_customer__",    "Customers",     Customer,    "name"),
        ("__system_vendor__",      "Vendors",       Vendor,      "name"),
        ("__system_rawmaterial__", "Raw Materials", RawMaterial, "name"),
        ("__system_endproduct__",  "EndProduct",    None,        "name"),
        ("__system_department__",  "Departments",   Department,  "name"),
        ("__system_branch__",      "Branches",      Branch,      "name"),
    ]
    # import EndProduct locally to avoid re-importing at module level
    from .database import EndProduct as _EP
    _sys[3] = ("__system_endproduct__", "End Products", _EP, "name")

    for sys_id, sys_name, model, name_col in _sys:
        rows = db.query(model).filter(
            model.tenant_id == tenant_id,
            model.is_deleted == False,
        ).order_by(getattr(model, name_col)).all()
        items = [getattr(r, name_col) for r in rows if getattr(r, name_col, None)]
        if items:
            result.append({"id": sys_id, "name": sys_name, "items": items, "system": True})

    custom = db.query(CustomReferenceList).filter(
        CustomReferenceList.tenant_id == tenant_id,
        CustomReferenceList.is_deleted == False,
        CustomReferenceList.is_active != False,
    ).order_by(CustomReferenceList.list_name).all()
    for l in custom:
        items = [i.value for i in l.items if i.is_active and not i.is_deleted]
        result.append({"id": l.id, "name": l.list_name, "items": items, "system": False})

    return _json_module.dumps(result)


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
from .templates_env import templates  # shared instance — has all filters





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
    from .auth import get_user_modules
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first() if user else None
    modules = get_user_modules(user) if user else []
    return {"request": request, "user": user,
            "L": _L(db, user), "unread": _unread(db, user),
            "has_inventory":       has_feature(tenant, "INVENTORY",       db) if tenant else False,
            "has_fms":             has_feature(tenant, "FMS",             db) if tenant else False,
            "has_knowledge_repo":  has_feature(tenant, "KNOWLEDGE_REPO",  db) if tenant else False,
            "has_checklists": True,  # core feature, always available
            "has_sales":            "SALES"     in modules and (has_feature(tenant, "SALES_MODULE",     db) if tenant else False),
            "has_inventory_module": "INVENTORY" in modules and (has_feature(tenant, "INVENTORY_MODULE",  db) if tenant else False),
            "has_sales_analytics":  (has_feature(tenant, "SALES_ANALYTICS", db) if tenant else False)
                                     and (has_feature(tenant, "SALES_MODULE", db) if tenant else False)
                                     and "SALES" in modules and user.role in ("ADMIN", "MANAGER") if user else False,
            "user_modules":         modules,
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

def _planned_dates(ticket, stages) -> dict:
    """Calculate (planned_start, planned_end) for each stage from ticket.created_at + TaT.
    Returns dict mapping stage_id → (planned_start, planned_end).
    If any stage has no target_tat_hours, that stage and all subsequent get (None, None)."""
    sorted_stages = sorted([s for s in stages if not getattr(s, "is_deleted", False)], key=lambda s: s.order)
    result = {}
    cursor = ticket.created_at
    for s in sorted_stages:
        ps = cursor
        if s.target_tat_hours:
            pe = cursor + timedelta(hours=s.target_tat_hours)
        else:
            # No TAT defined — give a 1-minute placeholder so plan dates are always present
            pe = cursor + timedelta(minutes=1)
        result[s.id] = (ps, pe)
        cursor = pe
    return result


def _cross_stage_cf(db: Session, ticket_id: str, stages: list, exclude_history_id: str = None) -> dict:
    """Aggregate custom field values from every stage this ticket has already
    passed through, keyed by both field id (UUID) and field label, so that
    formula columns and 'already captured' field dedup can look up values
    captured in earlier stages — not just the current stage's own fields."""
    import json as _json
    cf_all: dict = {}
    hist = (
        db.query(FMSStageHistory)
        .filter(FMSStageHistory.ticket_id == ticket_id)
        .order_by(FMSStageHistory.entered_at)
        .all()
    )
    for h in hist:
        if exclude_history_id and h.id == exclude_history_id:
            continue
        if not h.custom_fields_data_json:
            continue
        try:
            cf_data = _json.loads(h.custom_fields_data_json)
        except Exception:
            continue
        cf_all.update(cf_data)  # id-keyed
        src_stage = next((s for s in stages if s.id == h.stage_id), None)
        if src_stage and src_stage.custom_fields_json:
            try:
                for fdef in _json.loads(src_stage.custom_fields_json):
                    fid = fdef.get("id", "")
                    lbl = fdef.get("label", "")
                    if fid and lbl and fid in cf_data:
                        cf_all[lbl] = cf_data[fid]  # label-keyed
            except Exception:
                pass
    return cf_all


def _live_eval_formulas(cf_all: dict, stages: list) -> None:
    """Live-evaluate formula-type custom columns for display, using already-
    captured cross-stage values. Mutates cf_all in place.
    Formula columns are normally only computed and persisted when the stage
    that defines them is transitioned out (see the transition handler below).
    That leaves a display gap: a later stage's formula column that references
    an earlier stage's value shows blank until the later stage itself closes,
    even though the referenced value is already known. Recomputing here from
    the same aggregated cf_all closes that gap for table/stage-view display."""
    for s in stages:
        if not s.custom_fields_json:
            continue
        try:
            fdefs = _json.loads(s.custom_fields_json)
        except Exception:
            continue
        for fdef in fdefs:
            fid = fdef.get("id", "")
            if fdef.get("field_type") != "formula" or not fid or fid in cf_all:
                continue
            steps = fdef.get("formula_steps") or []
            result = None
            for i, step in enumerate(steps):
                raw = cf_all.get(step.get("col_id", ""), "")
                try:
                    val = float(raw)
                except (ValueError, TypeError):
                    result = None
                    break
                if i == 0:
                    result = val
                    continue
                op = step.get("op", "+")
                if op == "+":   result += val
                elif op == "-": result -= val
                elif op == "*": result *= val
                elif op == "/":
                    if val == 0:
                        result = None
                        break
                    result /= val
            if result is not None:
                computed = str(int(result)) if result == int(result) else f"{result:.4f}".rstrip("0")
                cf_all[fid] = computed
                lbl = fdef.get("label", "")
                if lbl:
                    cf_all[lbl] = computed


def _tat_pct(history_row: FMSStageHistory, stage: FMSStage = None) -> Optional[int]:
    """Return 0-100+ percentage of TaT used.
    Prefers ticket-specific planned_end/planned_start from history row;
    falls back to stage.target_tat_hours for legacy rows without a schedule."""
    until = history_row.exited_at or datetime.utcnow()
    elapsed_h = (until - history_row.entered_at).total_seconds() / 3600
    # Use ticket-specific planned window if available
    if history_row.planned_start and history_row.planned_end:
        planned_h = (history_row.planned_end - history_row.planned_start).total_seconds() / 3600
        if planned_h > 0:
            return int(elapsed_h / planned_h * 100)
    # Fall back to flow-level stage target
    if stage and stage.target_tat_hours:
        return int(elapsed_h / stage.target_tat_hours * 100)
    return None

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


@router.post("/flows/{flow_id}/ticket-form")
async def fms_flow_save_ticket_form(
    flow_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save the custom ticket-creation form fields for a flow. Accessible to all admins."""
    from fastapi.responses import JSONResponse
    flow = _get_flow(db, flow_id, user.tenant_id)
    body = await request.json()
    fields = body.get("fields", [])

    # Validate and normalise each field definition
    valid_types = {"text", "number", "date", "longtext", "select", "ref_list", "__priority__", "__due_date__"}
    clean = []
    for f in fields:
        ftype = (f.get("field_type") or "text").strip().lower()
        label = (f.get("label") or "").strip()
        if not label or ftype not in valid_types:
            continue
        builtin_types = {"__priority__", "__due_date__"}
        field_id = ftype if ftype in builtin_types else (f.get("id") or new_id())
        entry = {
            "id": field_id,
            "label": label,
            "field_type": ftype,
            "required": bool(f.get("required", False)),
            "order": int(f.get("order", len(clean))),
        }
        if ftype == "select":
            raw_opts = f.get("options", [])
            entry["options"] = [o.strip() for o in raw_opts if str(o).strip()]
        elif ftype == "ref_list":
            entry["ref_list_id"]   = (f.get("ref_list_id") or "").strip()
            entry["ref_list_name"] = (f.get("ref_list_name") or "").strip()
        clean.append(entry)

    flow.ticket_form_fields_json = _json.dumps(clean)
    flow.updated_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"ok": True, "field_count": len(clean)})


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
    content = (await file.read()).decode("utf-8-sig", errors="replace").lstrip(chr(65279))
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


@router.get("/tickets/export")
def fms_tickets_export(
    request: Request,
    flow_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    f_priority: List[str] = Query([]),
    f_assignee_id: List[str] = Query([]),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "CSV_EXPORT", db):
        return RedirectResponse("/plan?upgrade=CSV_EXPORT", status_code=302)
    tid = user.tenant_id
    q = db.query(FMSTicket).filter(FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False)
    if user.role == "MANAGER":
        team_ids = [u.id for u in db.query(User).filter(
            User.manager_id == user.id, User.is_deleted == False).all()]
        team_ids.append(user.id)
        hist_tids = [h.ticket_id for h in db.query(FMSStageHistory).filter(
            FMSStageHistory.assignee_id.in_(team_ids)).all()]
        help_tids = [h.ticket_id for h in db.query(FMSTicketHelper).filter(
            FMSTicketHelper.user_id.in_(team_ids)).all()]
        all_ids = set(hist_tids) | set(help_tids)
        q = q.filter((FMSTicket.current_assignee_id.in_(team_ids)) | (FMSTicket.id.in_(all_ids)))
    elif user.role == "EMPLOYEE":
        help_tids = [h.ticket_id for h in db.query(FMSTicketHelper).filter(
            FMSTicketHelper.user_id == user.id).all()]
        hist_tids = [h.ticket_id for h in db.query(FMSStageHistory).filter(
            FMSStageHistory.assignee_id == user.id).all()]
        emp_ids = set(help_tids) | set(hist_tids)
        q = q.filter((FMSTicket.current_assignee_id == user.id) | (FMSTicket.id.in_(emp_ids)))
    if flow_id:
        q = q.filter(FMSTicket.flow_id == flow_id)
    if status_filter:
        q = q.filter(FMSTicket.status == status_filter)
    if f_priority:
        q = q.filter(FMSTicket.priority.in_(f_priority))
    if f_assignee_id:
        q = q.filter(FMSTicket.current_assignee_id.in_(f_assignee_id))
    if date_from:
        try:
            q = q.filter(FMSTicket.created_at >= datetime.fromisoformat(date_from))
        except Exception:
            pass
    if date_to:
        try:
            q = q.filter(FMSTicket.created_at <= datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59))
        except Exception:
            pass
    tickets = q.order_by(FMSTicket.created_at.desc()).all()

    def fmt_dt(dt):
        return dt.strftime("%d %b %Y") if dt else ""

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Ticket ID", "Title", "Flow", "Current Stage", "Status", "Priority",
                "Assigned To", "Created By", "Created Date", "Due Date", "Work Order No."])
    for t in tickets:
        assignee = t.current_assignee
        w.writerow([
            t.display_id or "", t.title,
            t.flow.name if t.flow else "",
            t.current_stage.name if t.current_stage else "",
            t.status, t.priority,
            assignee.name if assignee else "",
            t.created_by.name if t.created_by else "",
            fmt_dt(t.created_at), fmt_dt(t.due_at),
            t.wo_number or "",
        ])
    filename = f"fms_tickets_{datetime.utcnow().strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/dashboard", response_class=HTMLResponse)
def fms_dashboard(
    request: Request,
    flow_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    view: str = "stage_table",
    dept_id: List[str] = Query([]),
    manager_id: List[str] = Query([]),
    branch_id: List[str] = Query([]),
    month: Optional[str] = None,
    status_filter: Optional[str] = None,
    f_priority: List[str] = Query([]),
    f_assignee_id: List[str] = Query([]),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    my_work: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """FMS Dashboard — summary strip + flow cards + swimlane/stage-table/consolidated view."""
    # View name normalisation — map legacy names and accept new names
    _view_map = {"list": "stage", "stage_table": "stage", "consolidated": "table"}
    view = _view_map.get(view, view)
    if view not in ("table", "stage", "timeline", "swimlane"):
        view = "stage"
    import logging as _log, traceback as _tb
    try:
        return _fms_dashboard_inner(
            request=request, flow_id=flow_id, stage_id=stage_id, view=view,
            dept_id=dept_id, manager_id=manager_id, branch_id=branch_id,
            month=month, status_filter=status_filter,
            f_priority=f_priority, f_assignee_id=f_assignee_id,
            date_from=date_from, date_to=date_to,
            my_work=my_work,
            user=user, db=db,
        )
    except Exception as _exc:
        _log.getLogger("fms.dashboard").error(
            "FMS DASHBOARD CRASH:\n%s", _tb.format_exc()
        )
        raise


def _fms_dashboard_inner(
    request, flow_id, stage_id, view, dept_id, manager_id, branch_id,
    month, status_filter, f_priority, f_assignee_id, date_from, date_to, user, db,
    my_work: int = 0,
):
    tid = user.tenant_id
    now = datetime.utcnow()

    # All active flows for this tenant
    all_flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tid, FMSFlow.is_active == True,
        FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.created_at).all()

    # Role-based flow visibility: Admin sees all; Manager sees team-involved flows;
    # Employee sees only flows they were ever part of.
    if user.role == "EMPLOYEE":
        emp_ticket_flow_ids: set = set()
        for t in db.query(FMSTicket.flow_id).filter(
            FMSTicket.tenant_id == tid,
            FMSTicket.is_deleted == False,
        ).filter(
            (FMSTicket.current_assignee_id == user.id) |
            FMSTicket.id.in_(
                db.query(FMSStageHistory.ticket_id).filter(
                    FMSStageHistory.assignee_id == user.id)
            ) |
            FMSTicket.stage_assignees_json.like(f'%"{user.id}"%')
        ).distinct():
            emp_ticket_flow_ids.add(t.flow_id)
        flows = [f for f in all_flows if f.id in emp_ticket_flow_ids]
    elif user.role == "MANAGER":
        mgr_team_ids = [u.id for u in db.query(User).filter(
            User.manager_id == user.id, User.is_deleted == False).all()]
        mgr_team_ids.append(user.id)
        mgr_flow_ids: set = set()
        # Flows created by the manager
        for f in db.query(FMSFlow.id).filter(FMSFlow.created_by_id == user.id):
            mgr_flow_ids.add(f.id)
        # Flows where team members are/were assigned to any ticket
        for t in db.query(FMSTicket.flow_id).filter(
            FMSTicket.tenant_id == tid,
            FMSTicket.is_deleted == False,
        ).filter(
            (FMSTicket.current_assignee_id.in_(mgr_team_ids)) |
            FMSTicket.id.in_(
                db.query(FMSStageHistory.ticket_id).filter(
                    FMSStageHistory.assignee_id.in_(mgr_team_ids))
            ) |
            FMSTicket.id.in_(
                db.query(FMSTicketHelper.ticket_id).filter(
                    FMSTicketHelper.user_id.in_(mgr_team_ids))
            )
        ).distinct():
            mgr_flow_ids.add(t.flow_id)
        flows = [f for f in all_flows if f.id in mgr_flow_ids]
    else:
        flows = all_flows

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
    emp_all_fms_ids: set = set()
    emp_upcoming_ids: set = set()
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
        hist_tids_emp = [h.ticket_id for h in db.query(FMSStageHistory).filter(
            FMSStageHistory.assignee_id == user.id).all()]
        # pre-assigned to a future stage on any active ticket
        upcoming_tids_emp = [t.id for t in db.query(FMSTicket).filter(
            FMSTicket.tenant_id == tid,
            FMSTicket.is_deleted == False,
            FMSTicket.stage_assignees_json.like(f'%"{user.id}"%'),
        ).all()]
        emp_all_fms_ids = set(helper_tids) | set(hist_tids_emp)
        emp_upcoming_ids = set(upcoming_tids_emp) - emp_all_fms_ids
        base_q = base_q.filter(
            (FMSTicket.current_assignee_id == user.id) |
            (FMSTicket.id.in_(emp_all_fms_ids)) |
            (FMSTicket.id.in_(emp_upcoming_ids))
        )

    # ── Apply filter-bar selections to KPI base query ────────────────────────
    if active_flow:
        base_q = base_q.filter(FMSTicket.flow_id == active_flow.id)
    if f_priority:
        base_q = base_q.filter(FMSTicket.priority.in_(f_priority))
    # Resolve assignee/dept/manager/branch filter (same logic as ticket list)
    _kpi_assignee_ids = None
    if f_assignee_id:
        _kpi_assignee_ids = list(f_assignee_id)
    elif dept_id or manager_id or branch_id:
        _fq = db.query(User).filter(
            User.tenant_id == tid, User.is_deleted == False, User.is_active == True)
        if dept_id:
            _fq = _fq.filter(User.department_id.in_(dept_id))
        if manager_id:
            _mgr_team = []
            for _mid in manager_id:
                _mgr_team += [u.id for u in db.query(User).filter(
                    User.manager_id == _mid, User.tenant_id == tid,
                    User.is_deleted == False).all()]
                _mgr_team.append(_mid)
            _fq = _fq.filter(User.id.in_(_mgr_team))
        if branch_id:
            _br_dept_ids = [d.id for d in db.query(Department).filter(
                Department.branch_id.in_(branch_id), Department.tenant_id == tid,
                Department.is_deleted == False).all()]
            _fq = _fq.filter(User.department_id.in_(_br_dept_ids))
        _kpi_assignee_ids = [u.id for u in _fq.all()]
    if _kpi_assignee_ids is not None:
        base_q = base_q.filter(FMSTicket.current_assignee_id.in_(_kpi_assignee_ids))

    active_tickets = base_q.filter(
        FMSTicket.status.notin_(["COMPLETED", "CLOSED"])).count()
    flagged_count  = base_q.filter(FMSTicket.is_flagged == True).count()
    awaiting_count = base_q.filter(FMSTicket.status == "ACTIVE").count()

    tat_breaches = 0
    open_tickets = base_q.filter(
        FMSTicket.status.notin_(["COMPLETED", "CLOSED"])).all()
    for t in open_tickets:
        h = _open_history(db, t.id)
        if not h:
            continue
        # Prefer ticket-specific planned_end; fall back to stage target
        if h.planned_end:
            if now > h.planned_end:
                tat_breaches += 1
        elif t.current_stage and t.current_stage.target_tat_hours:
            elapsed = (now - h.entered_at).total_seconds() / 3600
            if elapsed > t.current_stage.target_tat_hours:
                tat_breaches += 1

    # Compliance: completed stage history rows with a planned window or stage target
    all_history = db.query(FMSStageHistory).join(
        FMSTicket, FMSStageHistory.ticket_id == FMSTicket.id
    ).filter(
        FMSTicket.tenant_id == tid,
        FMSStageHistory.exited_at != None,
    ).all()
    scoreable = [
        h for h in all_history
        if (h.planned_start and h.planned_end) or
           (h.stage and h.stage.target_tat_hours)
    ]
    if scoreable:
        def _on_time(h):
            actual_h = (h.exited_at - h.entered_at).total_seconds() / 3600
            if h.planned_start and h.planned_end:
                target_h = (h.planned_end - h.planned_start).total_seconds() / 3600
            else:
                target_h = h.stage.target_tat_hours
            return actual_h <= target_h
        on_time = sum(1 for h in scoreable if _on_time(h))
        compliance = int(on_time / len(scoreable) * 100)
    else:
        compliance = 0

    # ── Per-flow ticket counts for flow cards ─────────────────────────────────
    flow_counts = {}
    for f in flows:
        flow_counts[f.id] = db.query(FMSTicket).filter(
            FMSTicket.flow_id == f.id, FMSTicket.is_deleted == False,
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        ).count()

    # ── Filter data (loaded for all views) ───────────────────────────────────
    _depts_raw = db.query(Department).filter(
        Department.tenant_id == tid, Department.is_deleted == False
    ).order_by(Department.name).all()
    _seen_d: set = set()
    departments = [d for d in _depts_raw if d.name not in _seen_d and not _seen_d.add(d.name)]

    _mgrs_raw = db.query(User).filter(
        User.tenant_id == tid, User.role.in_(["MANAGER", "ADMIN"]),
        User.is_deleted == False, User.is_active == True,
    ).order_by(User.name).all()
    _seen_m: set = set()
    managers = [m for m in _mgrs_raw if m.name not in _seen_m and not _seen_m.add(m.name)]

    _branches_raw = db.query(Branch).filter(
        Branch.tenant_id == tid, Branch.is_deleted == False
    ).order_by(Branch.name).all()
    _seen_b: set = set()
    branches = [b for b in _branches_raw if b.name not in _seen_b and not _seen_b.add(b.name)]

    # Resolve combined assignee filter from multi-value params (all are lists now)
    filter_assignee_ids = None  # None = no filter applied
    if f_assignee_id:
        filter_assignee_ids = list(f_assignee_id)
    elif dept_id or manager_id or branch_id:
        filt_q = db.query(User).filter(
            User.tenant_id == tid, User.is_deleted == False, User.is_active == True
        )
        if dept_id:
            filt_q = filt_q.filter(User.department_id.in_(dept_id))
        if manager_id:
            mgr_team = []
            for mid in manager_id:
                mgr_team += [u.id for u in db.query(User).filter(
                    User.manager_id == mid, User.tenant_id == tid,
                    User.is_deleted == False).all()]
                mgr_team.append(mid)
            filt_q = filt_q.filter(User.id.in_(mgr_team))
        if branch_id:
            br_dept_ids = [d.id for d in db.query(Department).filter(
                Department.branch_id.in_(branch_id), Department.tenant_id == tid,
                Department.is_deleted == False).all()]
            filt_q = filt_q.filter(User.department_id.in_(br_dept_ids))
        filter_assignee_ids = [u.id for u in filt_q.all()]

    # Priority filter list
    priority_filter = list(f_priority) if f_priority else []

    # Parse date range filters
    from datetime import datetime as _dtp
    filter_date_from = None
    filter_date_to = None
    if date_from:
        try:
            filter_date_from = _dtp.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    if date_to:
        try:
            filter_date_to = _dtp.strptime(date_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59)
        except ValueError:
            pass

    # ── Swimlane data ─────────────────────────────────────────────────────────
    tickets_by_stage: dict = {}
    tat_info: dict = {}
    if active_flow and view == "swimlane":  # swimlane kept for legacy access
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
                (FMSTicket.current_assignee_id == user.id) |
                (FMSTicket.id.in_(emp_all_fms_ids)) |
                (FMSTicket.id.in_(emp_upcoming_ids))
            )
        if filter_assignee_ids is not None:
            swimlane_q = swimlane_q.filter(
                FMSTicket.current_assignee_id.in_(filter_assignee_ids))
        if priority_filter:
            swimlane_q = swimlane_q.filter(FMSTicket.priority.in_(priority_filter))
        if filter_date_from:
            swimlane_q = swimlane_q.filter(FMSTicket.created_at >= filter_date_from)
        if filter_date_to:
            swimlane_q = swimlane_q.filter(FMSTicket.created_at <= filter_date_to)

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

    # ── List view data (legacy — kept for any remaining references) ───────────
    list_tickets = []
    if view == "_legacy_list":
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
            list_q = list_q.filter(
                (FMSTicket.current_assignee_id == user.id) |
                (FMSTicket.id.in_(emp_all_fms_ids)) |
                (FMSTicket.id.in_(emp_upcoming_ids))
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
        # Assignee/dept/manager/branch filter (shared computation above)
        if filter_assignee_ids is not None:
            list_q = list_q.filter(
                FMSTicket.current_assignee_id.in_(filter_assignee_ids))
        # Priority filter
        if priority_filter:
            list_q = list_q.filter(FMSTicket.priority.in_(priority_filter))
        # Date range filter (takes priority over month)
        if filter_date_from or filter_date_to:
            if filter_date_from:
                list_q = list_q.filter(FMSTicket.created_at >= filter_date_from)
            if filter_date_to:
                list_q = list_q.filter(FMSTicket.created_at <= filter_date_to)
        elif month:
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

        if active_stage and view in ("stage", "stage_table"):
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
                q = q.filter(
                    (FMSTicket.current_assignee_id == user.id) |
                    (FMSTicket.id.in_(emp_all_fms_ids))
                )
            if my_work:
                q = q.filter(FMSTicket.current_assignee_id == user.id)
            if priority_filter:
                q = q.filter(FMSTicket.priority.in_(priority_filter))
            if filter_assignee_ids is not None:
                q = q.filter(FMSTicket.current_assignee_id.in_(filter_assignee_ids))
            if filter_date_from:
                q = q.filter(FMSTicket.created_at >= filter_date_from)
            if filter_date_to:
                q = q.filter(FMSTicket.created_at <= filter_date_to)

            raw = q.order_by(FMSTicket.created_at.desc()).all()
            for t in raw:
                h = _open_history(db, t.id)
                if h and active_stage.target_tat_hours:
                    pct = _tat_pct(h, active_stage)
                    tc = "green" if pct < 50 else "amber" if pct < 90 else "red"
                else:
                    pct, tc = None, "gray"
                sub_cols = _submodule_cols(db, t, active_stage.sub_module_tag)
                # Aggregate custom field values from ALL stage history entries.
                # Keys are UUID-based (per field def), so we also add label-keyed
                # entries using the stage's field definitions — this allows cross-stage
                # column display when two stages share the same label name.
                # Build a label-keyed lookup of all custom field values across every
                # stage this ticket has visited, using a direct DB query (avoids
                # lazy-load uncertainty). Also index by UUID so reused-column refs work.
                import json as _json
                cf_all: dict = {}
                if t.ticket_custom_fields_json:
                    try:
                        cf_all.update(_json.loads(t.ticket_custom_fields_json))
                    except Exception:
                        pass
                all_hist = (
                    db.query(FMSStageHistory)
                    .filter(FMSStageHistory.ticket_id == t.id)
                    .order_by(FMSStageHistory.entered_at)
                    .all()
                )
                for sh in all_hist:
                    if not sh.custom_fields_data_json:
                        continue
                    try:
                        cf_data = _json.loads(sh.custom_fields_data_json)
                    except Exception:
                        continue
                    cf_all.update(cf_data)  # UUID-keyed
                    src_stage = next(
                        (s for s in stage_table_stages if s.id == sh.stage_id), None
                    )
                    if src_stage and src_stage.custom_fields_json:
                        try:
                            for fdef in _json.loads(src_stage.custom_fields_json):
                                fid = fdef.get("id", "")
                                lbl = fdef.get("label", "")
                                if fid and lbl and fid in cf_data:
                                    cf_all[lbl] = cf_data[fid]  # label-keyed
                        except Exception:
                            pass
                _live_eval_formulas(cf_all, stage_table_stages)
                planned_end = None
                pd = _planned_dates(t, stage_table_stages)
                if active_stage.id in pd:
                    planned_end = pd[active_stage.id][1]
                stage_tickets.append({
                    "ticket": t,
                    "tat_pct": pct,
                    "tat_color": tc,
                    "assignee_name": t.current_assignee.name if t.current_assignee else "—",
                    "sub": sub_cols,
                    "entered_at": h.entered_at if h else None,
                    "cf_all": cf_all,
                    "planned_end": planned_end,
                })

    # Next/prev stage maps (used by Mark Done and Move Backward modals)
    next_stage_map: dict = {}
    prev_stage_map: dict = {}
    for i, s in enumerate(stage_table_stages):
        if i + 1 < len(stage_table_stages):
            next_stage_map[s.id] = stage_table_stages[i + 1]
        if i > 0:
            prev_stage_map[s.id] = stage_table_stages[i - 1]

    # ── Table view: full journey per ticket — all stages as columns ───────────
    table_tickets = []
    if view == "table" and active_flow and stage_table_stages:
        tq = db.query(FMSTicket).filter(
            FMSTicket.flow_id == active_flow.id,
            FMSTicket.is_deleted == False,
        )
        if user.role == "MANAGER":
            tq = tq.filter(
                (FMSTicket.current_assignee_id.in_(team_ids)) |
                (FMSTicket.id.in_(mgr_all_fms_ids))
            )
        elif user.role == "EMPLOYEE":
            tq = tq.filter(
                (FMSTicket.current_assignee_id == user.id) |
                (FMSTicket.id.in_(emp_all_fms_ids)) |
                (FMSTicket.id.in_(emp_upcoming_ids))
            )
        if priority_filter:
            tq = tq.filter(FMSTicket.priority.in_(priority_filter))
        if filter_assignee_ids is not None:
            tq = tq.filter(FMSTicket.current_assignee_id.in_(filter_assignee_ids))
        if filter_date_from:
            tq = tq.filter(FMSTicket.created_at >= filter_date_from)
        if filter_date_to:
            tq = tq.filter(FMSTicket.created_at <= filter_date_to)

        import json as _json
        for t in tq.order_by(FMSTicket.created_at.desc()).all():
            pd = _planned_dates(t, stage_table_stages)

            # Build latest-visit dict from history: stage_id → most recent row
            all_hist = db.query(FMSStageHistory).filter(
                FMSStageHistory.ticket_id == t.id
            ).order_by(FMSStageHistory.entered_at).all()
            visit_map: dict = {}
            for h in all_hist:
                visit_map[h.stage_id] = h  # last assignment wins (most recent visit)

            # Build cumulative cf_all across all history entries (UUID + label keyed)
            cf_cumulative: dict = {}
            if t.ticket_custom_fields_json:
                try:
                    cf_cumulative.update(_json.loads(t.ticket_custom_fields_json))
                except Exception:
                    pass
            for h in all_hist:
                if not h.custom_fields_data_json:
                    continue
                try:
                    cf_data = _json.loads(h.custom_fields_data_json)
                except Exception:
                    continue
                cf_cumulative.update(cf_data)
                src_stage = next(
                    (s for s in stage_table_stages if s.id == h.stage_id), None
                )
                if src_stage and src_stage.custom_fields_json:
                    try:
                        for fdef in _json.loads(src_stage.custom_fields_json):
                            fid = fdef.get("id", "")
                            lbl = fdef.get("label", "")
                            if fid and lbl and fid in cf_data:
                                cf_cumulative[lbl] = cf_data[fid]
                    except Exception:
                        pass
            _live_eval_formulas(cf_cumulative, stage_table_stages)

            stages_info = []
            for s in stage_table_stages:
                ps, pe = pd.get(s.id, (None, None))
                h = visit_map.get(s.id)
                actual_start = h.entered_at if h else None
                actual_end   = h.exited_at  if h else None
                delay_h      = None
                delay_positive = None
                if actual_end and pe:
                    delay_secs = (actual_end - pe).total_seconds()
                    delay_h    = round(abs(delay_secs) / 3600, 1)
                    delay_positive = delay_secs > 0
                is_current = (s.id == t.current_stage_id)
                assignee_name = h.assignee.name if (h and h.assignee) else "—"
                stages_info.append({
                    "stage":          s,
                    "planned_start":  ps,
                    "planned_end":    pe,
                    "actual_start":   actual_start,
                    "actual_end":     actual_end,
                    "delay_h":        delay_h,
                    "delay_positive": delay_positive,
                    "is_current":     is_current,
                    "visited":        h is not None,
                    "cf":             cf_cumulative,
                    "assignee_name":  assignee_name,
                })

            table_tickets.append({
                "ticket":        t,
                "assignee_name": t.current_assignee.name if t.current_assignee else "—",
                "stages":        stages_info,
            })

    # ── Timeline view ─────────────────────────────────────────────────────────
    timeline_data = []
    if view == "timeline" and active_flow and stage_table_stages:
        for s in stage_table_stages:
            tq2 = db.query(FMSTicket).filter(
                FMSTicket.current_stage_id == s.id,
                FMSTicket.is_deleted == False,
                FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
            )
            if user.role == "MANAGER":
                tq2 = tq2.filter(
                    (FMSTicket.current_assignee_id.in_(team_ids)) |
                    (FMSTicket.id.in_(mgr_all_fms_ids))
                )
            elif user.role == "EMPLOYEE":
                tq2 = tq2.filter(
                    (FMSTicket.current_assignee_id == user.id) |
                    (FMSTicket.id.in_(emp_all_fms_ids))
                )
            t_list = tq2.all()
            stage_items = []
            for t in t_list:
                h = _open_history(db, t.id)
                time_at_s = int((now - h.entered_at).total_seconds()) if h else 0
                stage_items.append({
                    "ticket": t,
                    "time_at_s": time_at_s,
                    "assignee_name": t.current_assignee.name if t.current_assignee else "—",
                })
            timeline_data.append({
                "stage": s,
                "count": len(stage_items),
                "tickets": stage_items,
            })

    # ── Consolidated table view (legacy — maps to 'table' now) ───────────────
    import json as _json
    consolidated_rows = []
    if view == "_legacy_consolidated" and active_flow and stage_table_stages:
        # All tickets in this flow (role-scoped)
        cq = db.query(FMSTicket).filter(
            FMSTicket.flow_id == active_flow.id,
            FMSTicket.is_deleted == False,
        )
        if user.role == "MANAGER":
            cq = cq.filter(
                (FMSTicket.current_assignee_id.in_(team_ids)) |
                (FMSTicket.id.in_(mgr_all_fms_ids))
            )
        elif user.role == "EMPLOYEE":
            cq = cq.filter(
                (FMSTicket.current_assignee_id == user.id) |
                (FMSTicket.id.in_(emp_all_fms_ids)) |
                (FMSTicket.id.in_(emp_upcoming_ids))
            )
        if priority_filter:
            cq = cq.filter(FMSTicket.priority.in_(priority_filter))
        if filter_assignee_ids is not None:
            cq = cq.filter(FMSTicket.current_assignee_id.in_(filter_assignee_ids))
        if filter_date_from:
            cq = cq.filter(FMSTicket.created_at >= filter_date_from)
        if filter_date_to:
            cq = cq.filter(FMSTicket.created_at <= filter_date_to)
        all_flow_tickets = cq.order_by(FMSTicket.created_at.desc()).all()

        for t in all_flow_tickets:
            # Get the most recent history row per stage
            histories = db.query(FMSStageHistory).filter(
                FMSStageHistory.ticket_id == t.id
            ).order_by(FMSStageHistory.entered_at).all()

            stage_data = {}  # stage_id -> dict
            for h in histories:
                cf_data = {}
                if h.custom_fields_data_json:
                    try:
                        cf_data = _json.loads(h.custom_fields_data_json)
                    except Exception:
                        cf_data = {}
                duration_h = None
                if h.entered_at and h.exited_at:
                    duration_h = round((h.exited_at - h.entered_at).total_seconds() / 3600, 1)
                # Keep latest visit per stage
                stage_data[h.stage_id] = {
                    "assignee_name": h.assignee.name if h.assignee else "—",
                    "entered_at":    h.entered_at,
                    "exited_at":     h.exited_at,
                    "duration_h":    duration_h,
                    "planned_start": h.planned_start,
                    "planned_end":   h.planned_end,
                    "cf":            cf_data,
                    "is_active":     h.exited_at is None,
                }

            consolidated_rows.append({
                "ticket": t,
                "stage_data": stage_data,
            })

    from .linked_entities import get_linked_entity_options as _geo
    entity_options = _geo(db, tid)

    # Per-ticket manager-override window flag for stage view (2h after BACKWARD move)
    override_eligible: set = set()
    if user.role in ("ADMIN", "MANAGER") and active_stage and view == "stage":
        for row in stage_tickets:
            t = row["ticket"]
            last_back = db.query(FMSStageHistory).filter(
                FMSStageHistory.ticket_id == t.id,
                FMSStageHistory.direction == "BACKWARD",
            ).order_by(FMSStageHistory.entered_at.desc()).first()
            if last_back and (now - last_back.entered_at).total_seconds() < 7200:
                override_eligible.add(t.id)

    ticket_form_fields = []
    if active_flow and active_flow.ticket_form_fields_json:
        try:
            ticket_form_fields = [
                f for f in _json.loads(active_flow.ticket_form_fields_json)
                if f.get("field_type") not in ("__priority__", "__due_date__")
            ]
        except Exception:
            ticket_form_fields = []

    return templates.TemplateResponse(request, "fms/dashboard.html", _ctx(
        request, user, db,
        flows=flows, active_flow=active_flow,
        flow_counts=flow_counts,
        view=view,
        ticket_form_fields=ticket_form_fields,
        # Stage view (formerly stage_table)
        stage_table_stages=stage_table_stages,
        active_stage=active_stage,
        stage_tickets=stage_tickets,
        stage_ticket_counts=stage_ticket_counts,
        next_stage_map=next_stage_map,
        prev_stage_map=prev_stage_map,
        override_eligible=override_eligible,
        # Table view (per-ticket flat list with planned/actual dates)
        table_tickets=table_tickets,
        # Timeline view
        timeline_data=timeline_data,
        # swimlane (legacy)
        tickets_by_stage=tickets_by_stage,
        tat_info=tat_info,
        flagged_tickets=flagged_tickets,
        can_drag=can_drag,
        # consolidated (legacy — no longer shown in toggle)
        consolidated_rows=consolidated_rows,
        # list (legacy)
        list_tickets=list_tickets,
        departments=departments,
        managers=managers,
        branches=branches,
        # active filters (lists for multi-select)
        f_dept_id=list(dept_id),
        f_manager_id=list(manager_id),
        f_branch_id=list(branch_id),
        f_month=month or "",
        f_status=status_filter or "",
        f_priority=list(f_priority),
        f_assignee_id=list(f_assignee_id),
        f_date_from=date_from or "",
        f_date_to=date_to or "",
        employees=employees,
        entity_options=entity_options,
        # role-relative ticket classification for employee board symbols
        emp_upcoming_ids=emp_upcoming_ids,
        emp_all_fms_ids=emp_all_fms_ids,
        my_work=my_work,
        # summary strip
        active_tickets=active_tickets,
        tat_breaches=tat_breaches,
        flagged_count=flagged_count,
        awaiting_count=awaiting_count,
        compliance=compliance,
        now=now,
        ref_lists_json=_build_ref_lists_json(user.tenant_id, db),
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
    import json as _json
    flow = _get_flow(db, flow_id, user.tenant_id)
    stage = db.query(FMSStage).filter(
        FMSStage.id == starting_stage_id,
        FMSStage.flow_id == flow_id).first()
    if not stage:
        raise HTTPException(400, "Invalid starting stage")

    # Collect per-stage pre-assignments: stage_assignee_<stage_id>
    form_data = dict(await request.form())

    # Collect ticket creation form fields (defined in flow's ticket_form_fields_json)
    ticket_form_fields = _json.loads(flow.ticket_form_fields_json or "[]")
    ticket_custom_fields: dict = {}
    for cf in ticket_form_fields:
        fid   = cf.get("id", "")
        ftype = cf.get("field_type", "")
        val   = str(form_data.get(f"cf__{fid}", "") or "").strip()
        if ftype == "__priority__":
            if val and val.upper() in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                priority = val.upper()
            continue
        if ftype == "__due_date__":
            if val:
                try:
                    due_at = val  # passed through to ticket creation below
                except Exception:
                    pass
            continue
        if val:
            ticket_custom_fields[fid] = val
    stage_assignees = {
        k[len("stage_assignee_"):]: v
        for k, v in form_data.items()
        if k.startswith("stage_assignee_") and v.strip()
    }
    stage_assignees[stage.id] = assignee_id
    stage_assignees_json = _json.dumps(stage_assignees)

    # Build stage schedule from form: stage_planned_end_<stage_id>
    # planned_start of stage N = planned_end of stage N-1
    # Auto-filled by JS from start_date + cumulative TAT, user can override per stage
    all_flow_stages = sorted(
        [s for s in flow.stages if not s.is_deleted], key=lambda s: s.order
    )
    stage_schedule: dict = {}
    start_date_str = form_data.get("schedule_start_date", "").strip()
    if start_date_str:
        try:
            cursor = datetime.fromisoformat(start_date_str)
            for fs in all_flow_stages:
                p_end_str = form_data.get(f"stage_planned_end_{fs.id}", "").strip()
                if p_end_str:
                    p_end = datetime.fromisoformat(p_end_str)
                else:
                    tat_h = fs.target_tat_hours or 24
                    p_end = cursor + timedelta(hours=tat_h)
                stage_schedule[fs.id] = {
                    "planned_start": cursor.isoformat(),
                    "planned_end":   p_end.isoformat(),
                }
                cursor = p_end
        except (ValueError, TypeError):
            stage_schedule = {}
    stage_schedule_json = _json.dumps(stage_schedule) if stage_schedule else None

    # Planned dates for the first stage history row
    first_sched = stage_schedule.get(stage.id, {})
    first_ps = datetime.fromisoformat(first_sched["planned_start"]) if first_sched.get("planned_start") else None
    first_pe = datetime.fromisoformat(first_sched["planned_end"])   if first_sched.get("planned_end")   else None

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
        stage_assignees_json=stage_assignees_json,
        stage_schedule_json=stage_schedule_json,
        ticket_custom_fields_json=_json.dumps(ticket_custom_fields) if ticket_custom_fields else None,
    )
    db.add(ticket)
    db.flush()

    tenant = db.query(Tenant).get(user.tenant_id)
    ticket.display_id = _next_fms_display_id(db, tenant)

    db.add(FMSStageHistory(
        ticket_id=ticket.id, stage_id=stage.id,
        stage_name=stage.name, assignee_id=assignee_id,
        direction="FORWARD",
        planned_start=first_ps,
        planned_end=first_pe,
    ))
    _log(db, ticket.id, user.id, "CREATED", title)
    _log(db, ticket.id, user.id, "STAGE_ENTERED", stage.name)
    db.commit()
    db.refresh(ticket)

    # P7-06: save linked entities
    from .linked_entities import save_linked_entities_from_form as _slf
    _slf(db, form_data, "FMS_TICKET", ticket.id, user.tenant_id, user.id)

    admins   = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for(db, assignee_id)
    notify_fms_stage_transition(
        user.tenant_id, ticket.id, ticket.title,
        stage.name, user.id, admins, managers, assignee_id)

    assignee_obj = db.query(User).filter(User.id == assignee_id).first()
    if assignee_obj:
        send_whatsapp_for_fms_ticket_created(db, ticket, assignee_obj)
    notify_fms_ticket_opened(db, ticket, assignee_obj, admins, managers)

    return _redirect(
        f"/fms/dashboard?view=stage&flow_id={flow_id}&stage_id={stage.id}&msg=Ticket+created"
    )


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
    return _redirect(
        f"/fms/dashboard?view=stage"
        f"{'&flow_id=' + ticket.flow_id if ticket.flow_id else ''}"
        f"{'&stage_id=' + ticket.current_stage_id if ticket.current_stage_id else ''}"
    )


# ── P7-08: Delete FMS Ticket ──────────────────────────────────────────────────

@router.post("/tickets/{ticket_id}/delete")
def fms_ticket_delete(
    ticket_id: str,
    flow_id: str = Form(""),
    stage_id: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    if ticket.status in ("COMPLETED", "CLOSED"):
        return_url = f"/fms/dashboard?view=stage"
        if flow_id: return_url += f"&flow_id={flow_id}"
        if stage_id: return_url += f"&stage_id={stage_id}"
        return _redirect(return_url + "&err=Tickets+with+terminal+status+cannot+be+deleted")
    history_count = db.query(FMSStageHistory).filter(
        FMSStageHistory.ticket_id == ticket_id,
        FMSStageHistory.exited_at != None,
    ).count()
    if history_count > 0:
        return_url = f"/fms/dashboard?view=stage"
        if flow_id: return_url += f"&flow_id={flow_id}"
        if stage_id: return_url += f"&stage_id={stage_id}"
        return _redirect(return_url + "&err=Tickets+with+activity+cannot+be+deleted")
    ticket.is_deleted = True
    _log(db, ticket_id, user.id, "DELETED", "Soft deleted by admin")
    db.commit()
    return_url = f"/fms/dashboard?view=stage"
    if flow_id: return_url += f"&flow_id={flow_id}"
    if stage_id: return_url += f"&stage_id={stage_id}"
    return _redirect(return_url)


@router.post("/tickets/{ticket_id}/notify")
def fms_ticket_notify(
    ticket_id: str,
    flow_id: str = Form(""),
    stage_id: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a manual in-app reminder notification to the current stage assignee."""
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(403, "Not authorised")
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    if not ticket.current_assignee_id:
        return_url = f"/fms/dashboard?view=stage"
        if flow_id: return_url += f"&flow_id={flow_id}"
        if stage_id: return_url += f"&stage_id={stage_id}"
        return _redirect(return_url + "&err=Ticket+has+no+assignee+to+notify")
    stage_name = ticket.current_stage.name if ticket.current_stage else "current stage"
    create_notification(
        db, user.tenant_id,
        user_id=ticket.current_assignee_id,
        notif_type="FMS_REMINDER",
        title=f"Reminder: {ticket.title}",
        body=f"This flow ticket is waiting for your action at stage {stage_name}.",
        link=f"/fms/dashboard?view=stage&flow_id={ticket.flow_id}&stage_id={ticket.current_stage_id}",
    )
    db.commit()
    assignee = db.query(User).filter(User.id == ticket.current_assignee_id).first()
    assignee_name = assignee.name if assignee else "assignee"
    return_url = f"/fms/dashboard?view=stage"
    if flow_id: return_url += f"&flow_id={flow_id}"
    if stage_id: return_url += f"&stage_id={stage_id}"
    from urllib.parse import quote
    return _redirect(return_url + f"&msg=Notification+sent+to+{quote(assignee_name)}")


# ── P7-07: Bulk upload FMS tickets ────────────────────────────────────────────

@router.get("/tickets/bulk-template")
def fms_bulk_template(
    flow_id: str = "",
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    import io as _io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    flow = None
    if flow_id:
        flow = db.query(FMSFlow).filter(
            FMSFlow.id == flow_id,
            FMSFlow.tenant_id == user.tenant_id,
            FMSFlow.is_deleted == False,
        ).first()
    if not flow:
        flow = db.query(FMSFlow).filter(
            FMSFlow.tenant_id == user.tenant_id,
            FMSFlow.is_active == True,
            FMSFlow.is_deleted == False,
        ).order_by(FMSFlow.name).first()

    stages = sorted([s for s in flow.stages if not s.is_deleted], key=lambda s: s.order) if flow else []

    wb = Workbook()
    ws = wb.active
    ws.title = "FMS Tickets"

    base_headers = ["Title *", "Priority", "Due Date (YYYY-MM-DD)", "WO Number", "Target Qty", "Qty Unit", "TaT Unit (Days/Hours)"]
    stage_headers = []
    for s in stages:
        stage_headers.append(f"{s.name} Assignee Phone")
        stage_headers.append(f"{s.name} TaT")
    all_headers = base_headers + stage_headers

    hdr_fill = PatternFill("solid", fgColor="1E293B")
    hdr_font = Font(bold=True, color="FFFFFF", size=11)
    for col, h in enumerate(all_headers, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = hdr_font; c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", wrap_text=True)

    inst_fill = PatternFill("solid", fgColor="374151")
    inst_font = Font(italic=True, color="9CA3AF", size=10)
    instructions = [
        "Required. Max 200 chars", "LOW / MEDIUM / HIGH / CRITICAL",
        "e.g. 2026-08-15", "Optional WO/PO ref",
        "Optional integer", "Optional: pcs/kg/m",
        "Days or Hours — applies to all TaT columns in this sheet",
    ]
    for s in stages:
        instructions.append(f"Phone of assignee for {s.name} (blank = flow default)")
        instructions.append(f"TaT for {s.name} (blank = flow default {s.target_tat_hours or '—'})")
    for col, inst in enumerate(instructions, 1):
        c = ws.cell(row=2, column=col, value=inst)
        c.font = inst_font; c.fill = inst_fill
        c.alignment = Alignment(wrap_text=True)

    sample = ["Sample Ticket WO-001", "MEDIUM", "2026-08-20", "WO-001", "100", "pcs", "Hours"]
    for s in stages:
        sample.append("")
        sample.append(str(s.target_tat_hours) if s.target_tat_hours else "24")
    for col, val in enumerate(sample, 1):
        ws.cell(row=3, column=col, value=val)

    col_widths = [30, 12, 18, 14, 10, 10, 20]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for i in range(len(stages)):
        ws.column_dimensions[get_column_letter(8 + i * 2 - 1)].width = 22
        ws.column_dimensions[get_column_letter(8 + i * 2)].width = 12
    ws.freeze_panes = "A3"

    buf = _io.BytesIO()
    wb.save(buf); buf.seek(0)
    fname = f"fms_{flow.name.replace(' ', '_')}_template.xlsx" if flow else "fms_template.xlsx"
    return StreamingResponse(
        iter([buf.read()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.post("/tickets/bulk-upload")
async def fms_bulk_upload(
    request: Request,
    file: UploadFile = File(...),
    upload_flow_id: str = Form(...),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    import io as _io
    from openpyxl import load_workbook

    flow = db.query(FMSFlow).filter(
        FMSFlow.id == upload_flow_id,
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_active == True,
        FMSFlow.is_deleted == False,
    ).first()
    if not flow:
        return _redirect("/fms/dashboard?err=Flow+not+found")

    stages = sorted([s for s in flow.stages if not s.is_deleted], key=lambda s: s.order)
    if not stages:
        return _redirect("/fms/dashboard?err=Flow+has+no+stages")

    content = await file.read()
    try:
        wb = load_workbook(filename=_io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        return _redirect("/fms/dashboard?err=Invalid+Excel+file+-+please+use+the+downloaded+template")

    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if len(all_rows) < 3:
        return _redirect("/fms/dashboard?err=File+too+short")
    if len(all_rows) - 2 > BULK_IMPORT_MAX_ROWS:
        return _redirect(f"/fms/dashboard?err=File+has+too+many+rows+-+maximum+{BULK_IMPORT_MAX_ROWS}")

    headers = [str(h).strip() if h is not None else "" for h in all_rows[0]]

    stage_phone_cols = {s.id: headers.index(f"{s.name} Assignee Phone") for s in stages if f"{s.name} Assignee Phone" in headers}
    stage_tat_cols   = {s.id: headers.index(f"{s.name} TaT")            for s in stages if f"{s.name} TaT"            in headers}
    tat_unit_col     = headers.index("TaT Unit (Days/Hours)") if "TaT Unit (Days/Hours)" in headers else None

    warnings = []
    created_count = 0
    tenant = db.query(Tenant).get(user.tenant_id)

    for row_num, row in enumerate(all_rows[2:], start=3):
        if not any(row):
            continue

        def cell(idx, r=row):
            return str(r[idx]).strip() if idx < len(r) and r[idx] is not None else ""

        title = cell(0)
        priority = cell(1).upper() or "MEDIUM"
        due_date_str = cell(2)
        wo_number = cell(3)
        target_qty_str = cell(4)
        qty_unit = cell(5)
        tat_unit_str = cell(tat_unit_col).lower() if tat_unit_col is not None else "hours"
        tat_mult = 24.0 if "day" in tat_unit_str else (1 / 60.0 if "min" in tat_unit_str else 1.0)

        if not title:
            warnings.append(f"Row {row_num}: Title required — skipped")
            continue
        if len(title) > 200:
            title = title[:200]
        if priority not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            priority = "MEDIUM"

        due_date = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M"):
            try:
                due_date = datetime.strptime(due_date_str, fmt); break
            except ValueError:
                pass
        if due_date is None:
            warnings.append(f"Row {row_num} ({title}): Invalid Due Date '{due_date_str}' — skipped")
            continue

        # Build per-stage assignees and schedule
        stage_assignees: dict = {}
        stage_schedule: dict = {}
        cursor = datetime.utcnow()
        for s in stages:
            assignee_id = s.default_assignee_id
            if s.id in stage_phone_cols:
                phone = cell(stage_phone_cols[s.id])
                if phone:
                    u = db.query(User).filter(
                        User.tenant_id == user.tenant_id, User.phone == phone,
                        User.is_deleted == False,
                    ).first()
                    if u:
                        assignee_id = u.id
                    else:
                        warnings.append(f"Row {row_num}: Phone '{phone}' not found for stage '{s.name}' — using default")
            if assignee_id:
                stage_assignees[s.id] = assignee_id

            tat_hours = float(s.target_tat_hours or 24)
            if s.id in stage_tat_cols:
                raw = cell(stage_tat_cols[s.id])
                try:
                    tat_hours = float(raw) * tat_mult
                except (ValueError, TypeError):
                    pass
            p_end = cursor + timedelta(hours=tat_hours)
            stage_schedule[s.id] = {"planned_start": cursor.isoformat(), "planned_end": p_end.isoformat()}
            cursor = p_end

        first_assignee_id = stage_assignees.get(stages[0].id) or stages[0].default_assignee_id
        first_sched = stage_schedule.get(stages[0].id, {})

        ticket = FMSTicket(
            tenant_id=user.tenant_id, flow_id=flow.id,
            current_stage_id=stages[0].id, title=title,
            priority=priority, wo_number=wo_number or None,
            target_qty=int(target_qty_str) if target_qty_str.isdigit() else None,
            qty_unit=qty_unit or None,
            current_assignee_id=first_assignee_id,
            due_at=due_date, created_by_id=user.id, status="ACTIVE",
            stage_assignees_json=_json.dumps(stage_assignees) if stage_assignees else None,
            stage_schedule_json=_json.dumps(stage_schedule) if stage_schedule else None,
        )
        db.add(ticket); db.flush()
        ticket.display_id = _next_fms_display_id(db, tenant)
        db.add(FMSStageHistory(
            ticket_id=ticket.id, stage_id=stages[0].id,
            stage_name=stages[0].name, assignee_id=first_assignee_id,
            direction="FORWARD",
            planned_start=datetime.fromisoformat(first_sched["planned_start"]) if first_sched.get("planned_start") else None,
            planned_end=datetime.fromisoformat(first_sched["planned_end"]) if first_sched.get("planned_end") else None,
        ))
        _log(db, ticket.id, user.id, "CREATED", f"History import: {title}")
        created_count += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return _redirect(f"/fms/dashboard?err=Import+failed+-+no+tickets+were+created:+{e}")

    from urllib.parse import quote as _q
    base_msg = f"{created_count} ticket(s) imported from '{flow.name}'"
    if warnings:
        base_msg += f". {len(warnings)} warning(s): {'; '.join(warnings[:3])}"
    return _redirect(f"/fms/dashboard?view=stage&flow_id={flow.id}&msg={_q(base_msg)}")


@router.get("/api/flow/{flow_id}/defaults")
def fms_api_flow_defaults(
    flow_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return stage names, default assignee names, and TaT values for bulk-create AJAX."""
    from fastapi.responses import JSONResponse
    flow = _get_flow(db, flow_id, user.tenant_id)
    stages = sorted([s for s in flow.stages if not s.is_deleted], key=lambda s: s.order)
    result = []
    for s in stages:
        assignee_name = None
        if s.default_assignee_id:
            u = db.query(User).get(s.default_assignee_id)
            assignee_name = u.name if u else None
        result.append({
            "id": s.id,
            "name": s.name,
            "default_assignee_name": assignee_name,
            "default_assignee_id": s.default_assignee_id,
            "target_tat_hours": s.target_tat_hours,
            "order": s.order,
        })
    employees = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.is_deleted == False,
        User.is_active == True,
    ).order_by(User.name).all()
    emp_list = [{"id": e.id, "name": e.name} for e in employees]
    ticket_form_fields = _json.loads(flow.ticket_form_fields_json or "[]")
    # Resolve ref_list options so the bulk-create table can render a dropdown
    for f in ticket_form_fields:
        if f.get("field_type") == "ref_list" and f.get("ref_list_id") and not f.get("options"):
            from .database import CustomReferenceList, CustomReferenceItem, Customer, Vendor, RawMaterial
            rid = f["ref_list_id"]
            if rid.startswith("__system_"):
                _sys_map = {
                    "__system_customer__": (Customer, "name"),
                    "__system_vendor__": (Vendor, "name"),
                    "__system_rawmaterial__": (RawMaterial, "name"),
                }
                model, col = _sys_map.get(rid, (None, None))
                if model:
                    rows = db.query(model).filter(model.tenant_id == user.tenant_id, model.is_deleted == False).all()
                    f["options"] = [getattr(r, col) for r in rows if getattr(r, col, None)]
            else:
                lst = db.query(CustomReferenceList).filter(
                    CustomReferenceList.id == rid,
                    CustomReferenceList.tenant_id == user.tenant_id,
                ).first()
                if lst:
                    f["options"] = [i.value for i in lst.items if i.is_active and not i.is_deleted]
    return JSONResponse({"stages": result, "employees": emp_list, "ticket_form_fields": ticket_form_fields})


@router.get("/tickets/bulk-create", response_class=HTMLResponse)
def fms_bulk_create_get(
    request: Request,
    flow_id: Optional[str] = Query(default=None),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    """A3-1: Bulk ticket creation form."""
    # Role-filtered flows (same logic as dashboard dropdown)
    all_flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id, FMSFlow.is_active == True,
        FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.name).all()
    if user.role == "EMPLOYEE":
        emp_flow_ids: set = set()
        for t in db.query(FMSTicket.flow_id).filter(
            FMSTicket.tenant_id == user.tenant_id,
            FMSTicket.is_deleted == False,
        ).filter(
            (FMSTicket.current_assignee_id == user.id) |
            FMSTicket.id.in_(db.query(FMSStageHistory.ticket_id).filter(FMSStageHistory.assignee_id == user.id)) |
            FMSTicket.stage_assignees_json.like(f'%"{user.id}"%')
        ).distinct():
            emp_flow_ids.add(t.flow_id)
        flows = [f for f in all_flows if f.id in emp_flow_ids]
    else:
        flows = all_flows
    # Pre-select flow passed from dashboard
    preselect_flow_id = flow_id if flow_id and any(f.id == flow_id for f in flows) else None
    return templates.TemplateResponse(request, "fms/bulk_create.html", _ctx(
        request, user, db, flows=flows, priorities=PRIORITIES,
        preselect_flow_id=preselect_flow_id,
    ))


@router.post("/tickets/bulk-create")
async def fms_bulk_create_post(
    request: Request,
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    """A3-1: Process bulk ticket creation — all-or-nothing validation."""
    form = await request.form()
    flow_id = (form.get("flow_id") or "").strip()
    flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id, FMSFlow.is_active == True,
        FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.name).all()

    def _reraise(error, row_errors=None):
        return templates.TemplateResponse(request, "fms/bulk_create.html", _ctx(
            request, user, db, flows=flows, priorities=PRIORITIES,
            error=error, row_errors=row_errors or [],
            saved_flow_id=flow_id, saved_form=form,
        ))

    if not flow_id:
        return _reraise("Please select a flow before submitting.")

    flow = _get_flow(db, flow_id, user.tenant_id)
    stages = sorted([s for s in flow.stages if not s.is_deleted], key=lambda s: s.order)
    if not stages:
        return _reraise("Selected flow has no stages configured.")

    first_stage = stages[0]
    ticket_form_fields = _json.loads(flow.ticket_form_fields_json or "[]")

    try:
        row_count = int(form.get("row_count") or "0")
    except ValueError:
        row_count = 0

    if row_count < 1:
        return _reraise("At least 1 ticket row is required.")
    if row_count > 50:
        return _reraise("Maximum 50 rows per bulk create.")

    today = datetime.utcnow().date()
    errors = []
    tickets_data = []

    tat_unit = (form.get("tat_unit") or "hours").strip().lower()
    tat_mult = 24.0 if tat_unit == "days" else (1 / 60.0 if tat_unit == "minutes" else 1.0)

    for i in range(row_count):
        wo_number = (form.get(f"row_wo_number_{i}") or "").strip()
        target_qty_str = (form.get(f"row_target_qty_{i}") or "").strip()
        qty_unit = (form.get(f"row_qty_unit_{i}") or "").strip()

        row_errs = []
        due_date = None
        priority = "MEDIUM"

        # Validate custom ticket form fields (__priority__ and __due_date__ are built-in special types)
        custom_field_values: dict = {}
        for cf in ticket_form_fields:
            val = (form.get(f"row_cf_{cf['id']}_{i}") or "").strip()
            ftype = cf.get("field_type", "")
            if ftype == "__priority__":
                if val and val.upper() in PRIORITIES:
                    priority = val.upper()
                continue  # not stored in custom_fields, mapped to ticket.priority
            if ftype == "__due_date__":
                if val:
                    try:
                        due_date = datetime.strptime(val, "%Y-%m-%d")
                        if due_date.date() <= today:
                            row_errs.append("Due date must be a future date")
                    except ValueError:
                        row_errs.append("Invalid due date format")
                elif cf.get("required"):
                    row_errs.append(f"'{cf['label']}' is required")
                continue  # not stored in custom_fields, mapped to ticket.due_at
            if cf.get("required") and not val:
                row_errs.append(f"'{cf['label']}' is required")
            if val:
                custom_field_values[cf["id"]] = val

        # Title is always auto-generated from the sequence — display_id is the human identifier
        title = f"Ticket-{i + 1}"

        if row_errs:
            errors.append({"row": i + 1, "title": f"Row {i + 1}", "errors": row_errs})
        else:
            # Collect per-stage assignee and TaT
            stage_assignees: dict = {}
            stage_schedule: dict = {}
            cursor = datetime.utcnow()
            for s in stages:
                aid = (form.get(f"row_stage_assignee_{i}_{s.id}") or "").strip() or s.default_assignee_id
                if aid:
                    stage_assignees[s.id] = aid
                tat_hours = float(s.target_tat_hours or 24)
                raw_tat = (form.get(f"row_stage_tat_{i}_{s.id}") or "").strip()
                if raw_tat:
                    try:
                        tat_hours = float(raw_tat) * tat_mult
                    except ValueError:
                        pass
                p_end = cursor + timedelta(hours=tat_hours)
                stage_schedule[s.id] = {"planned_start": cursor.isoformat(), "planned_end": p_end.isoformat()}
                cursor = p_end

            tickets_data.append({
                "title": title, "priority": priority,
                "due_date": due_date, "wo_number": wo_number or None,
                "target_qty": int(target_qty_str) if target_qty_str.isdigit() else None,
                "qty_unit": qty_unit or None,
                "stage_assignees": stage_assignees,
                "stage_schedule": stage_schedule,
                "custom_fields": custom_field_values,
            })

    if errors:
        return _reraise(None, errors)

    # All rows valid — create all tickets
    tenant = db.query(Tenant).get(user.tenant_id)
    admins = _admin_ids(db, user.tenant_id)

    created = []
    for td in tickets_data:
        first_assignee_id = td["stage_assignees"].get(first_stage.id) or first_stage.default_assignee_id
        first_sched = td["stage_schedule"].get(first_stage.id, {})
        ticket = FMSTicket(
            tenant_id=user.tenant_id, flow_id=flow_id,
            current_stage_id=first_stage.id, title=td["title"],
            priority=td["priority"], due_at=td["due_date"],
            wo_number=td["wo_number"],
            target_qty=td["target_qty"], qty_unit=td["qty_unit"],
            current_assignee_id=first_assignee_id,
            created_by_id=user.id, status="ACTIVE",
            stage_assignees_json=_json.dumps(td["stage_assignees"]) if td["stage_assignees"] else None,
            stage_schedule_json=_json.dumps(td["stage_schedule"]) if td["stage_schedule"] else None,
            ticket_custom_fields_json=_json.dumps(td["custom_fields"]) if td.get("custom_fields") else None,
        )
        db.add(ticket); db.flush()
        ticket.display_id = _next_fms_display_id(db, tenant)
        db.add(FMSStageHistory(
            ticket_id=ticket.id, stage_id=first_stage.id,
            stage_name=first_stage.name, assignee_id=first_assignee_id,
            direction="FORWARD",
            planned_start=datetime.fromisoformat(first_sched["planned_start"]) if first_sched.get("planned_start") else None,
            planned_end=datetime.fromisoformat(first_sched["planned_end"]) if first_sched.get("planned_end") else None,
        ))
        _log(db, ticket.id, user.id, "CREATED", td["title"])
        created.append((ticket, first_assignee_id))

    db.commit()

    # Notify stage-1 assignee for each created ticket
    for ticket, first_aid in created:
        if first_aid:
            mgrs = _manager_ids_for(db, first_aid)
            notify_fms_stage_transition(
                user.tenant_id, ticket.id, ticket.title,
                first_stage.name, user.id, admins, mgrs, first_aid,
            )

    from urllib.parse import quote as _q
    n = len(created)
    msg = _q(f"{n} ticket{'s' if n != 1 else ''} created successfully")
    return _redirect(f"/fms/dashboard?view=stage&flow_id={flow_id}&msg={msg}")





@router.post("/tickets/bulk-transition")
async def fms_bulk_transition(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A3-2: Bulk stage transition — partial success allowed."""
    if not _can_transition(user, FMSTicket(current_assignee_id=user.id)):
        # Allow admin/manager always; employees handled per-ticket below
        pass
    if user.role not in ("ADMIN", "MANAGER", "EMPLOYEE"):
        raise HTTPException(403, "Not authorised")

    form = await request.form()
    ticket_ids = form.getlist("ticket_ids")
    next_stage_id = (form.get("next_stage_id") or "").strip()
    flow_id = (form.get("flow_id") or "").strip()
    current_stage_id = (form.get("current_stage_id") or "").strip()

    if not ticket_ids or not next_stage_id:
        return _redirect(f"/fms/dashboard?view=stage&flow_id={flow_id}&err=Invalid+bulk+transition+request")

    if len(ticket_ids) > 20:
        return _redirect(f"/fms/dashboard?view=stage&flow_id={flow_id}&err=Maximum+20+tickets+per+bulk+transition")

    tid = user.tenant_id
    next_stage = db.query(FMSStage).filter(FMSStage.id == next_stage_id).first()
    if not next_stage:
        return _redirect(f"/fms/dashboard?view=stage&flow_id={flow_id}&err=Invalid+target+stage")

    admins = _admin_ids(db, tid)
    moved = 0
    skipped = []
    now = datetime.utcnow()

    for t_id in ticket_ids:
        ticket = db.query(FMSTicket).filter(
            FMSTicket.id == t_id,
            FMSTicket.tenant_id == tid,
            FMSTicket.is_deleted == False,
        ).first()
        if not ticket:
            skipped.append(f"{t_id[:8]}: not found")
            continue
        if ticket.status in ("COMPLETED", "CLOSED"):
            skipped.append(f"{ticket.display_id or t_id[:8]}: already completed/closed")
            continue
        if current_stage_id and ticket.current_stage_id != current_stage_id:
            skipped.append(f"{ticket.display_id or t_id[:8]}: no longer at this stage")
            continue
        if user.role == "EMPLOYEE" and not _can_transition(user, ticket):
            skipped.append(f"{ticket.display_id or t_id[:8]}: not your ticket")
            continue

        completion_note = (form.get(f"completion_note_{t_id}") or "").strip()
        cur_stage = ticket.current_stage
        if cur_stage and cur_stage.completion_note_required and not completion_note:
            skipped.append(f"{ticket.display_id or t_id[:8]}: completion note required")
            continue

        # Collect and validate per-ticket custom field values from bulk modal
        import json as _json
        custom_fields_data: dict = {}
        if cur_stage and cur_stage.custom_fields_json:
            try:
                field_defs = _json.loads(cur_stage.custom_fields_json)
            except Exception:
                field_defs = []

            missing_required = []
            for fdef in field_defs:
                fid = fdef.get("id", "")
                if fdef.get("field_type") == "formula":
                    continue
                # Values are submitted as bulk_cf__{fid}__{ticket_id}
                val = str(form.get(f"bulk_cf__{fid}__{t_id}", "") or "").strip()
                if fdef.get("required") and not val:
                    missing_required.append(fdef.get("label", fid))
                if val:
                    custom_fields_data[fid] = val

            if missing_required:
                skipped.append(
                    f"{ticket.display_id or t_id[:8]}: required field(s) not filled: "
                    f"{', '.join(missing_required)}"
                )
                continue

            # Second pass — evaluate formula columns. Merge in values captured
            # at earlier stages so cross-stage formula references resolve.
            all_flow_stages = db.query(FMSStage).filter(
                FMSStage.flow_id == ticket.flow_id, FMSStage.is_deleted == False
            ).all()
            _bulk_open_h = _open_history(db, t_id)
            _bulk_tff = {}
            if ticket.ticket_custom_fields_json:
                try:
                    _bulk_tff = _json.loads(ticket.ticket_custom_fields_json)
                except Exception:
                    pass
            formula_lookup = {
                **_bulk_tff,
                **_cross_stage_cf(db, t_id, all_flow_stages, exclude_history_id=_bulk_open_h.id if _bulk_open_h else None),
                **custom_fields_data,
            }

            def _eval_formula_bulk(steps: list) -> str | None:
                result = None
                for i, step in enumerate(steps):
                    raw = formula_lookup.get(step.get("col_id", ""), "")
                    try:
                        val = float(raw)
                    except (ValueError, TypeError):
                        return None
                    if i == 0:
                        result = val; continue
                    op = step.get("op", "+")
                    if op == "+":   result += val
                    elif op == "-": result -= val
                    elif op == "*": result *= val
                    elif op == "/":
                        if val == 0: return None
                        result /= val
                if result is None: return None
                return str(int(result)) if result == int(result) else f"{result:.4f}".rstrip("0")

            for fdef in field_defs:
                if fdef.get("field_type") != "formula": continue
                computed = _eval_formula_bulk(fdef.get("formula_steps") or [])
                if computed is not None:
                    custom_fields_data[fdef.get("id", "")] = computed

        # Enforce the flow's closing rule before letting a ticket land on the terminal stage
        if next_stage.is_terminal and ticket.flow and ticket.flow.closing_rule_json:
            try:
                rule = _json.loads(ticket.flow.closing_rule_json)
            except Exception:
                rule = None
            if rule and rule.get("col_id"):
                bulk_lookup = {**(formula_lookup if cur_stage and cur_stage.custom_fields_json else {}), **custom_fields_data}
                raw = bulk_lookup.get(rule["col_id"], "")
                try:
                    actual = float(raw)
                    op = rule.get("op", "=="); target = rule.get("value", 0)
                    ok = (
                        actual == target if op == "==" else
                        actual != target if op == "!=" else
                        actual <  target if op == "<"  else
                        actual <= target if op == "<=" else
                        actual >  target if op == ">"  else
                        actual >= target if op == ">=" else True
                    )
                except (ValueError, TypeError):
                    ok = False
                if not ok:
                    skipped.append(f"{ticket.display_id or t_id[:8]}: closing rule not met")
                    continue

        open_h = _open_history(db, t_id)
        if open_h:
            open_h.exited_at = now
            open_h.completion_note = completion_note or None
            open_h.custom_fields_data_json = _json.dumps(custom_fields_data) if custom_fields_data else None
            _log(db, t_id, user.id, "STAGE_EXITED",
                 f"From: {cur_stage.name if cur_stage else '?'}")

        new_assignee_id = next_stage.default_assignee_id or ticket.current_assignee_id
        db.add(FMSStageHistory(
            ticket_id=t_id, stage_id=next_stage_id,
            stage_name=next_stage.name, assignee_id=new_assignee_id,
            direction="FORWARD",
        ))
        ticket.current_stage_id = next_stage_id
        ticket.current_assignee_id = new_assignee_id
        ticket.updated_at = now
        if next_stage.is_terminal:
            ticket.status = "COMPLETED"
            ticket.completed_at = now
        else:
            ticket.status = "ACTIVE"
        _log(db, t_id, user.id, "STAGE_ENTERED", f"To: {next_stage.name}")

        # Notify new assignee
        if new_assignee_id:
            mgrs = _manager_ids_for(db, new_assignee_id)
            notify_fms_stage_transition(
                tid, t_id, ticket.title, next_stage.name, user.id,
                admins, mgrs, new_assignee_id,
            )
        moved += 1

    db.commit()

    from urllib.parse import quote as _q
    if skipped:
        msg = _q(f"{moved} moved; {len(skipped)} skipped: {'; '.join(skipped[:3])}")
        return _redirect(f"/fms/dashboard?view=stage&flow_id={flow_id}&stage_id={next_stage_id}&msg={msg}")
    msg = _q(f"{moved} ticket{'s' if moved != 1 else ''} moved to {next_stage.name}")
    return _redirect(f"/fms/dashboard?view=stage&flow_id={flow_id}&stage_id={next_stage_id}&msg={msg}")


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def fms_ticket_detail(
    ticket_id: str, request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Phase A2: detail page removed — redirect to Stage view
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    return _redirect(
        f"/fms/dashboard?view=stage"
        f"{'&flow_id=' + ticket.flow_id if ticket.flow_id else ''}"
        f"{'&stage_id=' + ticket.current_stage_id if ticket.current_stage_id else ''}"
    )
    # --- legacy detail page below (unreachable, kept for reference) ---
    ticket = _get_ticket(db, ticket_id, user.tenant_id)  # noqa: F841
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

        import json as _json
        try:
            stage_custom_fields = _json.loads(s.custom_fields_json or "[]")
        except Exception:
            stage_custom_fields = []

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
            "sub_module_tag": getattr(s, "sub_module_tag", None),
            "custom_fields":  stage_custom_fields,
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

    # Custom field definitions for the current active stage (for transition form)
    import json as _json
    current_stage_custom_fields = []
    if ticket.current_stage:
        try:
            current_stage_custom_fields = _json.loads(ticket.current_stage.custom_fields_json or "[]")
        except Exception:
            current_stage_custom_fields = []

    # Parse stage pre-assignments for the transition form auto-fill
    stage_assignees: dict = {}
    try:
        stage_assignees = _json.loads(ticket.stage_assignees_json or "{}")
    except Exception:
        stage_assignees = {}

    # Parse stage schedule for display in stage panels
    stage_schedule: dict = {}
    try:
        stage_schedule = _json.loads(ticket.stage_schedule_json or "{}")
    except Exception:
        stage_schedule = {}

    from .linked_entities import get_linked_entity_options as _geo
    from .database import LinkedEntityReference as _LER
    entity_options = _geo(db, user.tenant_id)
    linked_refs = db.query(_LER).filter(
        _LER.tenant_id == user.tenant_id,
        _LER.parent_type == "FMS_TICKET",
        _LER.parent_id == ticket_id,
    ).order_by(_LER.created_at).all()

    # Entity data for custom field entity_link dropdowns — Admin only
    entity_data = {}
    if user.role == "ADMIN":
        entity_data = {
            "customer": [{"id": c.id, "name": c.name} for c in
                         db.query(Customer).filter(Customer.tenant_id == user.tenant_id,
                                                   Customer.is_deleted == False).order_by(Customer.name).all()],
            "vendor": [{"id": v.id, "name": v.name} for v in
                       db.query(Vendor).filter(Vendor.tenant_id == user.tenant_id,
                                               Vendor.is_deleted == False).order_by(Vendor.name).all()],
            "raw_material": [{"id": r.id, "name": r.name} for r in
                             db.query(RawMaterial).filter(RawMaterial.tenant_id == user.tenant_id,
                                                          RawMaterial.is_deleted == False).order_by(RawMaterial.name).all()],
            "employee": [{"id": e.id, "name": e.name} for e in employees],
        }

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
        linked_refs=linked_refs,
        entity_data=entity_data,
        current_stage_custom_fields=current_stage_custom_fields,
        stage_assignees=stage_assignees,
        stage_schedule=stage_schedule,
        all_events=list(reversed(all_events)),
    ))


@router.post("/tickets/{ticket_id}/transition")
async def fms_transition(
    request: Request,
    ticket_id: str,
    next_stage_id: str = Form(""),
    new_assignee_id: str = Form(""),
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

    # Empty next_stage_id means "complete the current terminal stage"
    terminal_complete = not next_stage_id.strip()
    next_stage = None
    if not terminal_complete:
        next_stage = db.query(FMSStage).filter(
            FMSStage.id == next_stage_id,
            FMSStage.flow_id == ticket.flow_id).first()
        if not next_stage:
            raise HTTPException(400, "Invalid next stage")

    cur_stage  = ticket.current_stage
    open_h     = _open_history(db, ticket_id)

    if terminal_complete:
        direction = "FORWARD"
        next_order = (cur_stage.order if cur_stage else 0)
    else:
        # Determine direction (2-C-3/4)
        cur_order  = cur_stage.order  if cur_stage  else 0
        next_order = next_stage.order
        direction  = "BACKWARD" if next_order < cur_order else "FORWARD"

    # A1-5: Enforce linear stage movement (skip for terminal-complete)
    if not is_override and not terminal_complete:
        if direction == "FORWARD" and next_order != cur_order + 1:
            raise HTTPException(400, "Tickets can only move to the next stage in sequence")
        if direction == "BACKWARD" and next_order != cur_order - 1:
            raise HTTPException(400, "Tickets can only move to the previous stage in sequence")

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

    # Collect custom field values for current stage (A4 + Phase B)
    import json as _json
    custom_fields_data = {}
    if cur_stage:
        try:
            field_defs = _json.loads(cur_stage.custom_fields_json or "[]")
        except Exception:
            field_defs = []
        form_data = await request.form()

        # First pass — collect all non-formula values keyed by id
        missing_required = []
        for fdef in field_defs:
            fid = fdef.get("id", "")
            if fdef.get("field_type") == "formula":
                continue  # evaluated in second pass
            key = f"cf__{fid}"
            val = str(form_data.get(key, "") or "").strip()
            # Required-field enforcement only applies on FORWARD moves
            if fdef.get("required") and not val and direction != "BACKWARD":
                missing_required.append(fdef.get("label", fid))
            if val:
                custom_fields_data[fid] = val
        if missing_required:
            raise HTTPException(400, f"Required column(s) not filled: {', '.join(missing_required)}")

        # Second pass — evaluate formula columns server-side.
        # Formulas may reference columns captured in earlier stages (the
        # formula builder UI allows this), so merge in cross-stage values —
        # current-stage values take precedence on id collisions.
        all_flow_stages = db.query(FMSStage).filter(
            FMSStage.flow_id == ticket.flow_id, FMSStage.is_deleted == False
        ).all()
        _ticket_tff = {}
        if ticket.ticket_custom_fields_json:
            try:
                _ticket_tff = _json.loads(ticket.ticket_custom_fields_json)
            except Exception:
                pass
        formula_lookup = {
            **_ticket_tff,
            **_cross_stage_cf(db, ticket_id, all_flow_stages, exclude_history_id=open_h.id if open_h else None),
            **custom_fields_data,
        }

        def _eval_formula(steps: list) -> str | None:
            result = None
            for i, step in enumerate(steps):
                col_id = step.get("col_id", "")
                raw = formula_lookup.get(col_id, "")
                try:
                    val = float(raw)
                except (ValueError, TypeError):
                    return None  # referenced column missing or non-numeric
                if i == 0:
                    result = val
                    continue
                op = step.get("op", "+")
                if op == "+":   result += val
                elif op == "-": result -= val
                elif op == "*": result *= val
                elif op == "/":
                    if val == 0:
                        return None
                    result /= val
            if result is None:
                return None
            # Return as integer string if it's a whole number, else up to 4 dp
            return str(int(result)) if result == int(result) else f"{result:.4f}".rstrip("0")

        for fdef in field_defs:
            if fdef.get("field_type") != "formula":
                continue
            fid = fdef.get("id", "")
            steps = fdef.get("formula_steps") or []
            computed = _eval_formula(steps)
            if computed is not None:
                custom_fields_data[fid] = computed

    # Close current stage history row
    if open_h:
        open_h.exited_at              = datetime.utcnow()
        open_h.completion_note        = completion_note.strip() or None
        open_h.qty_completed          = qty
        open_h.evidence_url           = evidence_url
        open_h.evidence_filename      = evidence_filename
        open_h.custom_fields_data_json = _json.dumps(custom_fields_data) if custom_fields_data else None
        _log(db, ticket_id, user.id, "STAGE_EXITED",
             f"From: {cur_stage.name if cur_stage else '?'} | "
             f"note: {completion_note[:80]}" if completion_note else "")

    ticket.updated_at = datetime.utcnow()

    if terminal_complete:
        # Enforce the flow's closing rule (e.g. "excess quantity = 0") before
        # allowing the ticket to close, using the same cross-stage + ticket-form
        # value lookup used for formula columns above.
        flow_for_rule = ticket.flow
        if flow_for_rule and flow_for_rule.closing_rule_json:
            try:
                rule = _json.loads(flow_for_rule.closing_rule_json)
            except Exception:
                rule = None
            if rule and rule.get("col_id"):
                final_lookup = {**formula_lookup, **custom_fields_data} if cur_stage else custom_fields_data
                raw = final_lookup.get(rule["col_id"], "")
                try:
                    actual = float(raw)
                except (ValueError, TypeError):
                    raise HTTPException(400, "Cannot close ticket: the closing rule's column has no value yet.")
                op = rule.get("op", "==")
                target = rule.get("value", 0)
                ok = (
                    actual == target if op == "==" else
                    actual != target if op == "!=" else
                    actual <  target if op == "<"  else
                    actual <= target if op == "<=" else
                    actual >  target if op == ">"  else
                    actual >= target if op == ">=" else True
                )
                if not ok:
                    raise HTTPException(400, f"Cannot close ticket: closing rule not met ({actual} {op} {target} is false).")

        # Completing the current terminal stage — no new history row needed
        ticket.status       = "COMPLETED"
        ticket.completed_at = datetime.utcnow()
        _log(db, ticket_id, user.id, "COMPLETED",
             f"Completed terminal stage: {cur_stage.name if cur_stage else '?'}")
        db.commit()
        admins = _admin_ids(db, user.tenant_id)
        broadcast_sync(user.tenant_id, admins, FMS_STAGE_TRANSITION, {
            "ticket_id": ticket_id, "display_id": ticket.display_id,
            "title": ticket.title, "stage": cur_stage.name if cur_stage else "",
            "status": ticket.status,
        })
        return _redirect(
            f"/fms/dashboard?view=stage"
            f"{'&flow_id=' + ticket.flow_id if ticket.flow_id else ''}"
        )

    # Look up planned dates for next stage from ticket schedule
    import json as _json2
    _sched: dict = {}
    try:
        _sched = _json2.loads(ticket.stage_schedule_json or "{}")
    except Exception:
        _sched = {}
    _ns = _sched.get(next_stage_id, {})
    _nps = datetime.fromisoformat(_ns["planned_start"]) if _ns.get("planned_start") else None
    _npe = datetime.fromisoformat(_ns["planned_end"])   if _ns.get("planned_end")   else None

    # Create new stage history row (2-C-5: non-linear — always new row)
    db.add(FMSStageHistory(
        ticket_id=ticket_id, stage_id=next_stage_id,
        stage_name=next_stage.name, assignee_id=new_assignee_id,
        direction=direction,
        return_reason=return_reason.strip() or None,
        from_stage_id=cur_stage.id if cur_stage else None,
        from_stage_name=cur_stage.name if cur_stage else None,
        planned_start=_nps,
        planned_end=_npe,
    ))

    # Update ticket
    ticket.current_stage_id    = next_stage_id
    ticket.current_assignee_id = new_assignee_id

    if next_stage.is_terminal:
        flow_for_rule = ticket.flow
        if flow_for_rule and flow_for_rule.closing_rule_json:
            try:
                rule = _json.loads(flow_for_rule.closing_rule_json)
            except Exception:
                rule = None
            if rule and rule.get("col_id"):
                final_lookup = {**formula_lookup, **custom_fields_data} if cur_stage else custom_fields_data
                raw = final_lookup.get(rule["col_id"], "")
                try:
                    actual = float(raw)
                except (ValueError, TypeError):
                    raise HTTPException(400, "Cannot close ticket: the closing rule's column has no value yet.")
                op = rule.get("op", "==")
                target = rule.get("value", 0)
                ok = (
                    actual == target if op == "==" else
                    actual != target if op == "!=" else
                    actual <  target if op == "<"  else
                    actual <= target if op == "<=" else
                    actual >  target if op == ">"  else
                    actual >= target if op == ">=" else True
                )
                if not ok:
                    raise HTTPException(400, f"Cannot close ticket: closing rule not met ({actual} {op} {target} is false).")
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

    # WS broadcast + WhatsApp
    admins   = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for(db, new_assignee_id)
    notify_fms_stage_transition(
        user.tenant_id, ticket_id, ticket.title,
        next_stage.name, user.id, admins, managers, new_assignee_id)
    new_assignee_obj = db.query(User).filter(User.id == new_assignee_id).first()
    if new_assignee_obj:
        send_whatsapp_for_fms_stage_transition(
            db, user.tenant_id, ticket_id, ticket.title,
            next_stage.name, new_assignee_obj)
    audience = list(set(admins + managers + [new_assignee_id]))
    broadcast_sync(user.tenant_id, audience, FMS_STAGE_TRANSITION, {
        "ticket_id": ticket_id, "display_id": ticket.display_id,
        "title": ticket.title, "stage": next_stage.name,
        "status": ticket.status,
    })

    # Backward move: notify managers with a 2-hour override window message
    if direction == "BACKWARD":
        mgr_ids = _manager_ids_for(db, ticket.current_assignee_id)
        for mid in mgr_ids + admins:
            create_notification(
                db, user.tenant_id,
                user_id=mid,
                notif_type="FMS_BACKWARD_MOVE",
                title=f"Ticket returned: {ticket.title}",
                body=(f"{ticket.display_id or ticket_id} was returned to "
                      f"'{next_stage.name}'. Reason: {return_reason[:120]}. "
                      f"You can reverse this within 2 hours."),
                link=f"/fms/dashboard?view=stage&flow_id={ticket.flow_id}&stage_id={next_stage_id}",
            )
        db.commit()

    return _redirect(
        f"/fms/dashboard?view=stage"
        f"{'&flow_id=' + ticket.flow_id if ticket.flow_id else ''}"
        f"{'&stage_id=' + next_stage_id}"
    )


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
        return _redirect("/fms/dashboard?view=stage")

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
    return _redirect("/fms/dashboard?view=stage")


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
    # Redirect back to stage view at the ticket's current stage
    return _redirect(
        f"/fms/dashboard?view=stage"
        f"{'&flow_id=' + ticket.flow_id if ticket.flow_id else ''}"
        f"{'&stage_id=' + ticket.current_stage_id if ticket.current_stage_id else ''}"
    )


@router.post("/tickets/{ticket_id}/help_request")
def fms_help_request(
    ticket_id: str,
    reason: str = Form(...),
    helper_id: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Phase A2: Help Needed popup — set HELP_REQUESTED, notify admin/manager."""
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    if not reason.strip():
        raise HTTPException(400, "Reason is required")
    ticket.status = "HELP_REQUESTED"
    _log(db, ticket_id, user.id, "HELP_REQUESTED", reason.strip())
    # Notify all admins and managers
    admins = _admin_ids(db, user.tenant_id)
    mgrs   = _manager_ids_for(db, ticket.current_assignee_id)
    for uid in set(admins + mgrs):
        create_notification(
            db, user.tenant_id,
            user_id=uid,
            notif_type="FMS_HELP_NEEDED",
            title=f"Help needed: {ticket.title}",
            body=f"{user.name} needs help on {ticket.display_id or ticket_id}. Reason: {reason[:200]}",
            link=f"/fms/dashboard?view=stage&flow_id={ticket.flow_id}&stage_id={ticket.current_stage_id}",
        )
    if helper_id:
        existing = db.query(FMSTicketHelper).filter(
            FMSTicketHelper.ticket_id == ticket_id,
            FMSTicketHelper.user_id == helper_id).first()
        if not existing:
            db.add(FMSTicketHelper(
                ticket_id=ticket_id, user_id=helper_id,
                added_by_id=user.id, reason=reason.strip()))
    ticket.updated_at = datetime.utcnow()
    db.commit()
    return _redirect(
        f"/fms/dashboard?view=stage"
        f"{'&flow_id=' + ticket.flow_id if ticket.flow_id else ''}"
        f"{'&stage_id=' + ticket.current_stage_id if ticket.current_stage_id else ''}"
        f"&msg=Help+request+sent"
    )


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
    my_compliance = int(my_ontime / my_total * 100) if my_total else 0

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

@router.post("/tickets/{ticket_id}/stage-data")
async def fms_save_stage_data(
    ticket_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save custom stage column data to the current open stage history row."""
    from fastapi.responses import JSONResponse
    ticket = db.query(FMSTicket).filter(
        FMSTicket.id == ticket_id,
        FMSTicket.tenant_id == user.tenant_id,
        FMSTicket.is_deleted == False,
    ).first()
    if not ticket:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    history = (
        db.query(FMSStageHistory)
        .filter(
            FMSStageHistory.ticket_id == ticket_id,
            FMSStageHistory.stage_id == ticket.current_stage_id,
            FMSStageHistory.exited_at == None,
        )
        .order_by(FMSStageHistory.entered_at.desc())
        .first()
    )
    if not history:
        # Fallback: find any open history row for this ticket
        history = (
            db.query(FMSStageHistory)
            .filter(
                FMSStageHistory.ticket_id == ticket_id,
                FMSStageHistory.exited_at == None,
            )
            .order_by(FMSStageHistory.entered_at.desc())
            .first()
        )
    if not history:
        # Create one if truly missing (edge case for legacy tickets)
        history = FMSStageHistory(
            id=new_id(), ticket_id=ticket_id, tenant_id=ticket.tenant_id,
            stage_id=ticket.current_stage_id, entered_at=datetime.utcnow(),
        )
        db.add(history)
    import json as _json
    body = await request.json()
    incoming = body.get("data", {})
    existing = _json.loads(history.custom_fields_data_json or "{}") if history.custom_fields_data_json else {}
    existing.update(incoming)

    # Evaluate formula columns using cross-stage values so references to prior
    # stage columns resolve (the client can only see current-stage inputs).
    cur_stage = ticket.current_stage
    if cur_stage and cur_stage.custom_fields_json:
        try:
            field_defs = _json.loads(cur_stage.custom_fields_json)
        except Exception:
            field_defs = []
        all_flow_stages = db.query(FMSStage).filter(
            FMSStage.flow_id == ticket.flow_id, FMSStage.is_deleted == False
        ).all()
        _tff = {}
        if ticket.ticket_custom_fields_json:
            try:
                _tff = _json.loads(ticket.ticket_custom_fields_json)
            except Exception:
                pass
        formula_lookup = {
            **_tff,
            **_cross_stage_cf(db, ticket.id, all_flow_stages, exclude_history_id=history.id),
            **existing,
        }

        def _eval_sd_formula(steps):
            result = None
            for i, step in enumerate(steps):
                raw = formula_lookup.get(step.get("col_id", ""), "")
                try:
                    val = float(raw)
                except (ValueError, TypeError):
                    return None
                if i == 0:
                    result = val; continue
                op = step.get("op", "+")
                if op == "+":   result += val
                elif op == "-": result -= val
                elif op == "*": result *= val
                elif op == "/":
                    if val == 0: return None
                    result /= val
            if result is None: return None
            return str(int(result)) if result == int(result) else f"{result:.4f}".rstrip("0")

        for fdef in field_defs:
            if fdef.get("field_type") != "formula": continue
            computed = _eval_sd_formula(fdef.get("formula_steps") or [])
            if computed is not None:
                existing[fdef.get("id", "")] = computed

    history.custom_fields_data_json = _json.dumps(existing)
    db.commit()
    return JSONResponse({"ok": True, "computed": {k: v for k, v in existing.items()}})


@router.get("/tickets/{ticket_id}/cf-carry-forward")
def fms_cf_carry_forward(
    ticket_id: str,
    stage_id: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return carry-forward pre-fill values for a ticket entering a stage.
    Looks up stage custom_fields_json, finds columns with carry_forward=true,
    then searches stage history for the most recent value per column id.
    Returns {column_id: value} for all columns that have a prior value.
    """
    import json as _json
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    stage = db.query(FMSStage).filter(
        FMSStage.id == stage_id,
        FMSStage.tenant_id == user.tenant_id,
    ).first()
    if not stage:
        return {"values": {}}

    try:
        field_defs = _json.loads(stage.custom_fields_json or "[]")
    except Exception:
        field_defs = []

    cf_ids = [f["id"] for f in field_defs if f.get("carry_forward") and f.get("id")]
    if not cf_ids:
        return {"values": {}}

    histories = db.query(FMSStageHistory).filter(
        FMSStageHistory.ticket_id == ticket_id,
        FMSStageHistory.custom_fields_data_json.isnot(None),
    ).order_by(FMSStageHistory.entered_at.desc()).all()

    values = {}
    remaining = set(cf_ids)
    for h in histories:
        if not remaining:
            break
        try:
            data = _json.loads(h.custom_fields_data_json)
        except Exception:
            continue
        for cid in list(remaining):
            if cid in data:
                values[cid] = data[cid]
                remaining.discard(cid)

    # Fall back to values captured on the ticket creation form (e.g. columns
    # reused from the Ticket Creation Form have no stage history to draw from).
    if remaining and ticket.ticket_custom_fields_json:
        try:
            tff = _json.loads(ticket.ticket_custom_fields_json)
        except Exception:
            tff = {}
        for cid in list(remaining):
            if cid in tff:
                values[cid] = tff[cid]
                remaining.discard(cid)

    return {"values": values}
