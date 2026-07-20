from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import ChecklistAssignment, MediaUpload, Ticket, User, get_db
from ..notifications import notify_checklist_completed
from ..uploads import save_upload
from ..ws_manager import CHECKLIST_COMPLETED, broadcast_sync
from .security import get_current_api_user, limiter

router = APIRouter(tags=["My Tasks / Checklists"])

CHECKLIST_OPEN_STATUSES = ("PENDING", "IN_PROGRESS", "OVERDUE")


def _scoped_assignment_query(db: Session, user: User, assignment_id: str):
    """Tenant filter is unconditional (defense in depth — see security audit
    Part 4); EMPLOYEE additionally scoped to their own assignments."""
    q = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.id == assignment_id,
        ChecklistAssignment.is_deleted == False,
        ChecklistAssignment.tenant_id == user.tenant_id,
    )
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(ChecklistAssignment.user_id == user.id)
    return q


class MyTaskItem(BaseModel):
    kind: str  # "ticket" | "checklist"
    id: str
    title: str
    status: str
    due_at: Optional[datetime]
    is_flagged: bool = False


class ChecklistAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    template_id: str
    user_id: str
    due_at: datetime
    completed_at: Optional[datetime]
    status: str
    delay_reason: Optional[str]
    is_flagged: bool


class CompleteChecklistRequest(BaseModel):
    delay_reason: Optional[str] = ""


@router.get("/my-tasks", response_model=list[MyTaskItem])
def my_tasks(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Aggregated action queue: open tickets assigned to the caller + their
    pending/overdue checklist assignments. Mirrors the core of the desktop
    My Tasks page (app/my_tasks.py) — the FMS and CRM follow-up lanes there
    are desktop-only for now and can be added here once the native My Tasks
    screen actually needs them."""
    items: list[MyTaskItem] = []

    tickets = db.query(Ticket).filter(
        Ticket.tenant_id == user.tenant_id, Ticket.current_assignee_id == user.id,
        Ticket.status == "OPEN", Ticket.is_deleted == False,
    ).order_by(Ticket.due_at.asc().nullslast()).all()
    for t in tickets:
        items.append(MyTaskItem(kind="ticket", id=t.id, title=t.title, status=t.status, due_at=t.due_at, is_flagged=t.is_flagged))

    assignments = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == user.tenant_id, ChecklistAssignment.user_id == user.id,
        ChecklistAssignment.status.in_(CHECKLIST_OPEN_STATUSES), ChecklistAssignment.is_deleted == False,
    ).order_by(ChecklistAssignment.due_at.asc()).all()
    for a in assignments:
        title = a.template.title if a.template else "Checklist"
        items.append(MyTaskItem(kind="checklist", id=a.id, title=title, status=a.status, due_at=a.due_at, is_flagged=a.is_flagged))

    items.sort(key=lambda i: i.due_at or datetime.max)
    return items


@router.get("/checklists/{assignment_id}", response_model=ChecklistAssignmentOut)
def get_checklist_assignment(assignment_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    a = _scoped_assignment_query(db, user, assignment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Checklist assignment not found")
    return a


@router.post("/checklists/{assignment_id}/ack", response_model=ChecklistAssignmentOut)
def start_checklist_assignment(assignment_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """PENDING -> IN_PROGRESS."""
    a = _scoped_assignment_query(db, user, assignment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Checklist assignment not found")
    if a.status == "PENDING":
        a.status = "IN_PROGRESS"
        db.commit()
        db.refresh(a)
    return a


@router.post("/checklists/{assignment_id}/complete", response_model=ChecklistAssignmentOut)
def complete_checklist_assignment(assignment_id: str, body: CompleteChecklistRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Evidence upload isn't part of this API pass (deferred to a dedicated
    media-upload endpoint) — assignments with evidence_required still get
    marked DONE here, same as the delay-reason gate below; wiring the actual
    evidence requirement into the mobile flow is a follow-up once the
    Checklists screen is designed."""
    a = _scoped_assignment_query(db, user, assignment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Checklist assignment not found")

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    is_overdue = a.status == "OVERDUE" or (a.due_at and a.due_at < today_start)
    if is_overdue and not (body.delay_reason or "").strip():
        raise HTTPException(status_code=400, detail="Delay reason is required for overdue assignments")

    a.status = "DONE"
    a.completed_at = datetime.utcnow()
    if (body.delay_reason or "").strip():
        a.delay_reason = body.delay_reason.strip()

    from ..main import _next_due_from, _admin_ids, _manager_ids_for_ticket
    admins = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for_ticket(db, user.tenant_id, user.id)
    notify_checklist_completed(db, a, admins, managers)

    tmpl = a.template
    if tmpl and getattr(tmpl, "is_recurring", True):
        next_due = _next_due_from(tmpl.frequency, a.due_at)
        existing = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == tmpl.id, ChecklistAssignment.user_id == a.user_id,
            ChecklistAssignment.due_at == next_due, ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
        ).first()
        if not existing:
            db.add(ChecklistAssignment(
                template_id=tmpl.id, tenant_id=a.tenant_id, user_id=a.user_id,
                due_at=next_due, evidence_required=bool(a.evidence_required or tmpl.evidence_required),
            ))

    db.commit()
    db.refresh(a)
    broadcast_sync(user.tenant_id, list(set(admins + managers)), CHECKLIST_COMPLETED, {
        "checklist": tmpl.title if tmpl else "", "completed_by": user.name,
    })
    return a


class EvidenceOut(BaseModel):
    file_name: str
    file_path: str
    file_type: str
    file_size: int


@router.post("/tasks/{task_id}/evidence", response_model=EvidenceOut)
@limiter.limit("30/minute")
async def upload_checklist_evidence(request: Request, task_id: str, evidence_file: UploadFile = File(...),
                                     user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Phase 0.5-B: dedicated evidence-upload endpoint referenced in the
    complete_checklist_assignment docstring above. Same storage pattern as
    the desktop complete_checklist route (app/main.py) — save_upload ->
    MediaUpload -> sets proof_url on the assignment."""
    a = _scoped_assignment_query(db, user, task_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Checklist assignment not found")

    # save_upload validates by real file content (magic bytes), not the
    # client-declared Content-Type header — see security audit Part 4.
    info = await save_upload(evidence_file, user.tenant_id, allowed_kinds=("image",))
    db.add(MediaUpload(
        tenant_id=user.tenant_id, entity_type="CHECKLIST_ASSIGNMENT",
        entity_id=task_id, uploaded_by_id=user.id, **info,
    ))
    a.proof_url = info["file_path"]
    db.commit()
    return info
