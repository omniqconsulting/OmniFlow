"""Checklists — native app (template-centric CRUD + assignment actions).

Coexists with api_v1/tasks.py's assignment-scoped /checklists/{id}/ack|
complete|evidence routes (those serve the My Tasks aggregator's own detail
flow and are left untouched). This module's routes are template-scoped
(/checklists, /checklists/templates/..., /checklists/assignments/{id}/...)
so there is no path collision.

Reuses the desktop's existing checklist business logic (app/main.py,
app/checklist_freq.py, app/notifications.py) by importing it inline inside
each function body — the same pattern api_v1/tasks.py already uses — rather
than re-deriving divergent formulas. No desktop route/template is edited."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import (
    Branch,
    ChecklistAssignment,
    ChecklistTemplate,
    Department,
    User,
    get_db,
)
from .features import require_feature
from .security import get_current_api_user, require_api_admin, require_api_manager

router = APIRouter(prefix="/checklists", tags=["Checklists"], dependencies=[Depends(require_feature("CHECKLISTS"))])

# Nearest legacy-`frequency` bucket for each E-14 frequency_type — used only
# as the fallback cadence _next_due_from() steps by on completion; the real
# rule for WEEKLY_CUSTOM/MONTHLY_DATE/YEARLY_DATE/NTH_WEEKDAY_* lives in
# frequency_config and is evaluated via checklist_freq.py.
_LEGACY_FREQUENCY = {
    "DAILY": "DAILY", "WEEKLY": "WEEKLY", "MONTHLY": "MONTHLY",
    "QUARTERLY": "QUARTERLY", "YEARLY": "YEARLY",
    "WEEKLY_CUSTOM": "WEEKLY", "MONTHLY_DATE": "MONTHLY", "YEARLY_DATE": "YEARLY",
    "NTH_WEEKDAY_MONTH": "MONTHLY", "NTH_WEEKDAY_QUARTER": "QUARTERLY",
}


def _visible_user_ids(db: Session, user: User) -> Optional[List[str]]:
    """None = no restriction (ADMIN sees the whole tenant)."""
    if user.role == "ADMIN":
        return None
    if user.role == "MANAGER":
        team = [u.id for u in db.query(User).filter(
            User.manager_id == user.id, User.is_deleted == False).all()]
        team.append(user.id)
        return team
    return [user.id]


def _resolve_targets(db: Session, tmpl: ChecklistTemplate, tid: str) -> List[User]:
    if tmpl.assigned_to_user_id:
        return db.query(User).filter(
            User.id == tmpl.assigned_to_user_id, User.tenant_id == tid,
            User.is_active == True, User.is_deleted == False).all()
    if tmpl.assigned_to_dept_id:
        return db.query(User).filter(
            User.department_id == tmpl.assigned_to_dept_id, User.tenant_id == tid,
            User.is_active == True, User.is_deleted == False).all()
    return db.query(User).filter(
        User.tenant_id == tid, User.role == (tmpl.assigned_to_role or "EMPLOYEE"),
        User.is_active == True, User.is_deleted == False).all()


def _first_due_for(tmpl: ChecklistTemplate, now: datetime) -> Optional[datetime]:
    """First due date for a just-created template. Custom-rule types use the
    next date actually matching the rule (today counts if it matches);
    standard types default to today's due-time cutoff if that's still ahead,
    else the next cadence step."""
    from ..checklist_freq import CUSTOM_FREQUENCY_TYPES, apply_due_time, next_custom_occurrence
    if getattr(tmpl, "frequency_type", None) in CUSTOM_FREQUENCY_TYPES:
        from datetime import timedelta
        return next_custom_occurrence(tmpl, now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1))
    today_due = apply_due_time(now.date(), tmpl)
    if today_due > now:
        return today_due
    from ..main import _next_due_from
    return _next_due_from(tmpl.frequency, today_due)


# ── Schemas ──────────────────────────────────────────────────────────────

class ChecklistItemOut(BaseModel):
    template_id: str
    assignment_id: Optional[str]
    title: str
    description: str
    frequency_type: Optional[str]
    frequency_label: str
    evidence_required: bool
    is_active: bool
    status: Optional[str]
    due_at: Optional[datetime]
    completed_at: Optional[datetime]
    failure_note: Optional[str]
    delay_reason: Optional[str]
    employee_id: Optional[str]
    employee_name: Optional[str]
    department_name: Optional[str]
    branch_name: Optional[str]
    manager_name: Optional[str]
    compliance_pct: int


class FilterOptionsOut(BaseModel):
    branches: List[dict]
    departments: List[dict]
    managers: List[dict]
    employees: List[dict]


class ChecklistFormIn(BaseModel):
    title: str
    description: str = ""
    frequency_type: str = "DAILY"
    dow_days: List[int] = []
    dom_day: Optional[int] = None
    doy_month: Optional[int] = None
    doy_day: Optional[int] = None
    nth: Optional[int] = None
    nth_weekday: Optional[int] = None
    is_recurring: bool = True
    due_time_mode: str = "ANYTIME"
    due_time: Optional[str] = None
    evidence_required: bool = False
    assigned_to_user_id: Optional[str] = None
    assigned_to_dept_id: Optional[str] = None
    assigned_to_role: Optional[str] = None


class CompleteChecklistIn(BaseModel):
    note: Optional[str] = ""


class FailChecklistIn(BaseModel):
    note: Optional[str] = ""


# ── Helpers shared by list/detail ───────────────────────────────────────

def _build_item(db: Session, tmpl: ChecklistTemplate, assignment: Optional[ChecklistAssignment],
                 employee: Optional[User]) -> ChecklistItemOut:
    from ..main import _format_frequency

    dept_name = branch_name = manager_name = None
    if employee:
        if employee.department_id:
            d = db.query(Department).filter(Department.id == employee.department_id).first()
            dept_name = d.name if d else None
        if employee.branch_id:
            b = db.query(Branch).filter(Branch.id == employee.branch_id).first()
            branch_name = b.name if b else None
        if employee.manager_id:
            m = db.query(User).filter(User.id == employee.manager_id).first()
            manager_name = m.name if m else None

    compliance_pct = 100
    if employee:
        all_a = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == tmpl.id,
            ChecklistAssignment.user_id == employee.id,
            ChecklistAssignment.is_deleted == False,
        ).all()
        total = len(all_a)
        done = sum(1 for a in all_a if a.status == "DONE")
        compliance_pct = round(done / total * 100) if total else 100

    return ChecklistItemOut(
        template_id=tmpl.id,
        assignment_id=assignment.id if assignment else None,
        title=tmpl.title,
        description=tmpl.description or "",
        frequency_type=tmpl.frequency_type or tmpl.frequency,
        frequency_label=_format_frequency(tmpl),
        evidence_required=bool(tmpl.evidence_required),
        is_active=bool(tmpl.is_active),
        status=assignment.status if assignment else None,
        due_at=assignment.due_at if assignment else None,
        completed_at=assignment.completed_at if assignment else None,
        failure_note=assignment.failure_note if assignment else None,
        delay_reason=assignment.delay_reason if assignment else None,
        employee_id=employee.id if employee else None,
        employee_name=employee.name if employee else None,
        department_name=dept_name,
        branch_name=branch_name,
        manager_name=manager_name,
        compliance_pct=compliance_pct,
    )


# ── List / detail / history ─────────────────────────────────────────────

@router.get("", response_model=List[ChecklistItemOut])
def list_checklists(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    tid = user.tenant_id
    visible_uids = _visible_user_ids(db, user)

    active_tmpl_ids = [t.id for t in db.query(ChecklistTemplate.id).filter(
        ChecklistTemplate.tenant_id == tid, ChecklistTemplate.is_deleted == False,
        ChecklistTemplate.is_active == True).all()]
    if not active_tmpl_ids:
        return []

    aq = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == tid, ChecklistAssignment.is_deleted == False,
        ChecklistAssignment.template_id.in_(active_tmpl_ids))
    if visible_uids is not None:
        aq = aq.filter(ChecklistAssignment.user_id.in_(visible_uids))
    rows = aq.order_by(ChecklistAssignment.due_at.asc()).all()

    # Pick, per (template_id, user_id), the current actionable assignment
    # (earliest PENDING/IN_PROGRESS/OVERDUE) or else the most recent
    # DONE/FAILED one.
    OPEN = ("PENDING", "IN_PROGRESS", "OVERDUE")
    current: dict = {}
    latest_closed: dict = {}
    for a in rows:
        key = (a.template_id, a.user_id)
        if a.status in OPEN:
            if key not in current:
                current[key] = a  # rows already ordered by due_at asc
        else:
            prev = latest_closed.get(key)
            if not prev or (a.completed_at and (not prev.completed_at or a.completed_at > prev.completed_at)):
                latest_closed[key] = a

    chosen: dict = dict(latest_closed)
    chosen.update(current)  # open assignment wins over a closed one for the same pair

    tmpl_cache: dict = {}
    user_cache: dict = {}
    items = []
    for (template_id, user_id), a in chosen.items():
        tmpl = tmpl_cache.get(template_id)
        if tmpl is None:
            tmpl = db.query(ChecklistTemplate).filter(ChecklistTemplate.id == template_id).first()
            tmpl_cache[template_id] = tmpl
        if not tmpl:
            continue
        emp = user_cache.get(user_id)
        if emp is None:
            emp = db.query(User).filter(User.id == user_id).first()
            user_cache[user_id] = emp
        items.append(_build_item(db, tmpl, a, emp))
    return items


@router.get("/filter-options", response_model=FilterOptionsOut)
def filter_options(user: User = Depends(require_api_manager), db: Session = Depends(get_db)):
    tid = user.tenant_id
    branches = db.query(Branch).filter(Branch.tenant_id == tid, Branch.is_deleted == False).order_by(Branch.name).all()
    departments = db.query(Department).filter(Department.tenant_id == tid, Department.is_deleted == False).order_by(Department.name).all()
    employees = db.query(User).filter(User.tenant_id == tid, User.is_deleted == False, User.is_active == True).order_by(User.name).all()
    managers = [e for e in employees if e.role in ("ADMIN", "MANAGER")] if user.role == "ADMIN" else []
    return FilterOptionsOut(
        branches=[{"id": b.id, "name": b.name} for b in branches],
        departments=[{"id": d.id, "name": d.name, "branch_id": d.branch_id} for d in departments],
        managers=[{"id": m.id, "name": m.name} for m in managers],
        employees=[{"id": e.id, "name": e.name, "department_id": e.department_id,
                    "branch_id": e.branch_id, "manager_id": e.manager_id, "role": e.role} for e in employees],
    )


@router.get("/templates/{template_id}", response_model=ChecklistItemOut)
def get_checklist_template(template_id: str, employee_id: Optional[str] = Query(None),
                            user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    tmpl = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.id == template_id, ChecklistTemplate.tenant_id == user.tenant_id,
        ChecklistTemplate.is_deleted == False).first()
    if not tmpl:
        raise HTTPException(404, "Checklist not found")
    uid = employee_id if (employee_id and user.role in ("ADMIN", "MANAGER")) else user.id
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == template_id, ChecklistAssignment.user_id == uid,
        ChecklistAssignment.is_deleted == False,
    ).order_by(ChecklistAssignment.due_at.desc()).first()
    emp = db.query(User).filter(User.id == uid).first()
    return _build_item(db, tmpl, a, emp)


class HistoryRecordOut(BaseModel):
    date: Optional[datetime]
    status: str
    note: Optional[str]


class ChecklistHistoryOut(BaseModel):
    title: str
    frequency_label: str
    done_count: int
    failed_count: int
    compliance_pct: int
    records: List[HistoryRecordOut]


@router.get("/templates/{template_id}/history", response_model=ChecklistHistoryOut)
def get_checklist_history(template_id: str, employee_id: Optional[str] = Query(None),
                           user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    from ..main import _format_frequency
    tmpl = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.id == template_id, ChecklistTemplate.tenant_id == user.tenant_id,
        ChecklistTemplate.is_deleted == False).first()
    if not tmpl:
        raise HTTPException(404, "Checklist not found")
    uid = employee_id if (employee_id and user.role in ("ADMIN", "MANAGER")) else user.id
    rows = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == template_id, ChecklistAssignment.user_id == uid,
        ChecklistAssignment.is_deleted == False,
    ).order_by(ChecklistAssignment.due_at.desc()).all()
    done = sum(1 for a in rows if a.status == "DONE")
    failed = sum(1 for a in rows if a.status == "FAILED")
    total = len(rows)
    return ChecklistHistoryOut(
        title=tmpl.title, frequency_label=_format_frequency(tmpl),
        done_count=done, failed_count=failed,
        compliance_pct=round(done / total * 100) if total else 100,
        records=[HistoryRecordOut(
            date=a.completed_at or a.due_at, status=a.status,
            note=a.failure_note or a.delay_reason,
        ) for a in rows],
    )


# ── Create / edit / delete (template) ───────────────────────────────────

def _apply_form(db: Session, tmpl: ChecklistTemplate, body: ChecklistFormIn) -> None:
    from ..main import _parse_frequency_fields
    ft, fc = _parse_frequency_fields(
        body.frequency_type,
        ",".join(str(d) for d in body.dow_days),
        str(body.dom_day) if body.dom_day is not None else "",
        str(body.doy_month) if body.doy_month is not None else "",
        str(body.doy_day) if body.doy_day is not None else "",
        str(body.nth) if body.nth is not None else "",
        str(body.nth_weekday) if body.nth_weekday is not None else "",
    )
    tmpl.title = body.title
    tmpl.description = body.description
    tmpl.frequency = _LEGACY_FREQUENCY.get(body.frequency_type, "DAILY")
    tmpl.frequency_type = ft
    tmpl.frequency_config = fc
    tmpl.due_time_mode = body.due_time_mode if body.due_time_mode == "FIXED_TIME" else "ANYTIME"
    tmpl.due_time = body.due_time or None
    tmpl.evidence_required = body.evidence_required
    tmpl.is_recurring = body.is_recurring
    tmpl.assigned_to_user_id = body.assigned_to_user_id or None
    tmpl.assigned_to_dept_id = body.assigned_to_dept_id or None
    tmpl.assigned_to_role = body.assigned_to_role or ("EMPLOYEE" if not body.assigned_to_user_id else None)


@router.post("/templates", response_model=ChecklistItemOut)
def create_checklist_template(body: ChecklistFormIn, user: User = Depends(require_api_manager), db: Session = Depends(get_db)):
    tid = user.tenant_id
    tmpl = ChecklistTemplate(tenant_id=tid)
    _apply_form(db, tmpl, body)
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)

    now = datetime.utcnow()
    due = _first_due_for(tmpl, now)
    created_assignment = None
    if due:
        from ..notifications import notify_checklist_assigned
        for target in _resolve_targets(db, tmpl, tid):
            a = ChecklistAssignment(
                template_id=tmpl.id, tenant_id=tid, user_id=target.id, due_at=due,
                evidence_required=bool(tmpl.evidence_required),
            )
            db.add(a)
            db.flush()
            notify_checklist_assigned(db, a)
            if target.id == user.id or created_assignment is None:
                created_assignment = a
        db.commit()

    emp = db.query(User).filter(User.id == (created_assignment.user_id if created_assignment else user.id)).first()
    return _build_item(db, tmpl, created_assignment, emp)


@router.put("/templates/{template_id}", response_model=ChecklistItemOut)
def update_checklist_template(template_id: str, body: ChecklistFormIn,
                               user: User = Depends(require_api_admin), db: Session = Depends(get_db)):
    tmpl = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.id == template_id, ChecklistTemplate.tenant_id == user.tenant_id,
        ChecklistTemplate.is_deleted == False).first()
    if not tmpl:
        raise HTTPException(404, "Checklist not found")
    _apply_form(db, tmpl, body)

    from ..main import _sync_pending_assignments
    _sync_pending_assignments(db, tmpl, user.tenant_id)
    db.commit()
    db.refresh(tmpl)

    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == tmpl.id, ChecklistAssignment.is_deleted == False,
    ).order_by(ChecklistAssignment.due_at.desc()).first()
    emp = db.query(User).filter(User.id == a.user_id).first() if a else None
    return _build_item(db, tmpl, a, emp)


@router.delete("/templates/{template_id}")
def delete_checklist_template(template_id: str, user: User = Depends(require_api_admin), db: Session = Depends(get_db)):
    tmpl = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.id == template_id, ChecklistTemplate.tenant_id == user.tenant_id).first()
    if not tmpl:
        raise HTTPException(404, "Checklist not found")
    tmpl.is_deleted = True
    tmpl.is_active = False
    db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == tmpl.id,
        ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
    ).update({"is_deleted": True}, synchronize_session=False)
    db.commit()
    return {"deleted": True}


# ── Assignment actions ───────────────────────────────────────────────────

def _reschedule_if_recurring(db: Session, a: ChecklistAssignment) -> None:
    tmpl = a.template
    if not (tmpl and getattr(tmpl, "is_recurring", True)):
        return
    from ..main import _next_due_from
    next_due = _next_due_from(tmpl.frequency, a.due_at)
    existing = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.template_id == tmpl.id, ChecklistAssignment.user_id == a.user_id,
        ChecklistAssignment.due_at == next_due, ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
    ).first()
    if not existing:
        db.add(ChecklistAssignment(
            template_id=tmpl.id, tenant_id=a.tenant_id, user_id=a.user_id, due_at=next_due,
            evidence_required=bool(a.evidence_required or tmpl.evidence_required),
        ))


@router.post("/assignments/{assignment_id}/complete", response_model=ChecklistItemOut)
def complete_checklist_assignment(assignment_id: str, body: CompleteChecklistIn,
                                   user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    q = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id, ChecklistAssignment.is_deleted == False)
    q = q if user.role in ("ADMIN", "MANAGER") else q.filter(ChecklistAssignment.user_id == user.id)
    a = q.filter(ChecklistAssignment.tenant_id == user.tenant_id).first()
    if not a:
        raise HTTPException(404, "Checklist assignment not found")

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    is_overdue = a.status == "OVERDUE" or (a.due_at and a.due_at < today_start)
    note = (body.note or "").strip()
    if is_overdue and not note:
        raise HTTPException(400, "Delay reason is required for overdue assignments")
    ev_required = bool(a.evidence_required or (a.template and a.template.evidence_required))
    if ev_required and not a.proof_url:
        raise HTTPException(400, "Evidence is required for this checklist — upload it before completing")

    a.status = "DONE"
    a.completed_at = datetime.utcnow()
    if note:
        a.delay_reason = note

    from ..main import _admin_ids, _manager_ids_for_ticket
    from ..notifications import notify_checklist_completed
    from ..ws_manager import CHECKLIST_COMPLETED, broadcast_sync
    admins = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for_ticket(db, user.tenant_id, user.id)
    notify_checklist_completed(db, a, admins, managers)
    _reschedule_if_recurring(db, a)
    db.commit()
    broadcast_sync(user.tenant_id, list(set(admins + managers)), CHECKLIST_COMPLETED, {
        "checklist": a.template.title if a.template else "", "completed_by": user.name,
    })
    emp = db.query(User).filter(User.id == a.user_id).first()
    return _build_item(db, a.template, a, emp)


@router.post("/assignments/{assignment_id}/fail", response_model=ChecklistItemOut)
def fail_checklist_assignment(assignment_id: str, body: FailChecklistIn,
                               user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    # Desktop restricts this to the assignee only, even for admin/manager —
    # replicated exactly (not "fixed") for parity.
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id, ChecklistAssignment.user_id == user.id,
        ChecklistAssignment.tenant_id == user.tenant_id).first()
    if not a:
        raise HTTPException(404, "Checklist assignment not found")
    a.status = "FAILED"
    a.completed_at = datetime.utcnow()
    a.failure_note = (body.note or "").strip() or None
    _reschedule_if_recurring(db, a)
    db.commit()
    emp = db.query(User).filter(User.id == a.user_id).first()
    return _build_item(db, a.template, a, emp)


@router.post("/assignments/{assignment_id}/notify")
def notify_checklist_assignment(assignment_id: str, user: User = Depends(require_api_manager), db: Session = Depends(get_db)):
    from ..notifications import create_notification
    a = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id, ChecklistAssignment.tenant_id == user.tenant_id,
        ChecklistAssignment.is_deleted == False).first()
    if not a:
        raise HTTPException(404, "Checklist assignment not found")
    title = a.template.title if a.template else "Checklist Reminder"
    due_str = a.due_at.strftime("%d %b, %I:%M %p") if a.due_at else "—"
    create_notification(db, user.tenant_id, a.user_id, "CHECKLIST_DUE_SOON",
                         f"Reminder: {title}", f"Due: {due_str}", "/checklists")
    db.commit()
    return {"notified": True}
