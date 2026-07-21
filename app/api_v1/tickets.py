from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import LinkedEntityReference, MediaUpload, Tenant, Ticket, TicketAssignee, TicketComment, TicketEvent, User, get_db
from ..linked_entities import get_linked_entity_options
from ..notifications import notify_delay_logged, notify_helper_added, notify_ticket_assigned
from ..uploads import save_upload
from ..ws_manager import TICKET_ASSIGNED, TICKET_COMMENTED, TICKET_STATUS_CHANGED, broadcast_sync
from .pagination import paginate_cursor
from .features import require_feature
from .schemas import Page
from .schemas import UserOut
from .security import get_current_api_user, limiter

router = APIRouter(prefix="/tickets", tags=["Tickets"], dependencies=[Depends(require_feature("TICKETS"))])


def _log_event(db, ticket_id, actor_id, event_type, detail=""):
    db.add(TicketEvent(ticket_id=ticket_id, actor_id=actor_id, event_type=event_type, detail=detail))


def _admin_ids(db: Session, tenant_id: str) -> list:
    return [u.id for u in db.query(User).filter(User.tenant_id == tenant_id, User.role == "ADMIN", User.is_deleted == False).all()]


def _manager_ids_for_ticket(db: Session, tenant_id: str, assignee_id: str) -> list:
    assignee = db.query(User).filter(User.id == assignee_id, User.tenant_id == tenant_id).first()
    if assignee and assignee.manager_id:
        return [assignee.manager_id]
    return []


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    display_id: Optional[str]
    title: str
    description: str
    priority: str
    status: str
    ticket_type: str
    ticket_category: str
    created_by_id: str
    created_by_name: Optional[str]
    current_assignee_id: Optional[str]
    assignee_name: Optional[str]
    due_at: Optional[datetime]
    acknowledged_at: Optional[datetime]
    closed_at: Optional[datetime]
    is_flagged: bool
    flagged_reason: Optional[str]
    evidence_required: bool
    created_at: datetime


class TicketCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    ticket_id: str
    user_id: str
    user_name: Optional[str]
    body: str
    created_at: datetime


class TicketEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    ticket_id: str
    actor_id: str
    actor_name: Optional[str]
    event_type: str
    detail: Optional[str]
    created_at: datetime


class AttachmentListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    file_name: str
    file_path: str
    file_type: Optional[str]
    file_size: Optional[int]
    uploaded_by_id: str
    created_at: datetime


class TicketHelperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    user_name: Optional[str]
    note: Optional[str]
    created_at: datetime


class HelperAddRequest(BaseModel):
    user_id: str
    note: str = ""


class LinkedEntityIn(BaseModel):
    entity_type: str  # CUSTOMER / END_PRODUCT / MATERIAL / VENDOR / CUSTOM_LIST / OTHER
    entity_id: Optional[str] = None
    entity_label: str = ""
    custom_text: Optional[str] = None


class LinkedEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    entity_type: str
    entity_id: Optional[str]
    entity_label: Optional[str]
    custom_text: Optional[str]
    created_at: datetime


class TicketCreateRequest(BaseModel):
    title: str
    description: str
    priority: str = "MEDIUM"
    assignee_id: str
    due_at: datetime
    evidence_required: bool = False
    ticket_category: str = "NORMAL"
    linked_entities: list[LinkedEntityIn] = []


class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    due_at: Optional[datetime] = None
    assignee_id: Optional[str] = None


class CommentCreateRequest(BaseModel):
    body: str


class FlagRequest(BaseModel):
    reason: str


class DelayRequest(BaseModel):
    reason: str


@router.get("", response_model=Page[TicketOut])
def list_tickets(
    status: Optional[str] = Query(None),
    priority: list[str] = Query([]),
    ticket_category: list[str] = Query([]),
    assignee_id: list[str] = Query([]),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(Ticket).filter(Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False)
    if user.role == "EMPLOYEE":
        q = q.filter(Ticket.current_assignee_id == user.id)

    # "ACKNOWLEDGED" is a pseudo-status: still OPEN but already acknowledged —
    # mirrors the same tab split the desktop /tickets list uses.
    if status == "ACKNOWLEDGED":
        q = q.filter(Ticket.status == "OPEN", Ticket.acknowledged_at.isnot(None))
    elif status == "OPEN":
        q = q.filter(Ticket.status == "OPEN", Ticket.acknowledged_at.is_(None))
    elif status:
        q = q.filter(Ticket.status == status)

    if priority:
        q = q.filter(Ticket.priority.in_(priority))
    if ticket_category:
        q = q.filter(Ticket.ticket_category.in_(ticket_category))
    if assignee_id:
        q = q.filter(Ticket.current_assignee_id.in_(assignee_id))
    if date_from:
        try:
            q = q.filter(Ticket.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Ticket.created_at <= datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59))
        except ValueError:
            pass

    rows, next_cursor = paginate_cursor(q, Ticket, cursor, limit)
    return Page(items=rows, next_cursor=next_cursor)


class LinkedEntityOptionOut(BaseModel):
    key: str
    label: str
    items: list[dict]


@router.get("/linked-entity-options", response_model=list[LinkedEntityOptionOut])
def linked_entity_options(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Setup-driven pick lists (Customers, End Products, Raw Materials, Vendors,
    custom reference lists) for linking a Setup record to a ticket — same source
    of truth as the desktop create-ticket form's Linked Entities section."""
    raw = get_linked_entity_options(db, user.tenant_id)
    labels = {"CUSTOMER": "Customers", "END_PRODUCT": "End Products", "MATERIAL": "Raw Materials", "VENDOR": "Vendors"}
    out = []
    for key, items in raw.items():
        if key.startswith("CUSTOM_LIST:"):
            _, list_id, list_name = key.split(":", 2)
            out.append(LinkedEntityOptionOut(key=f"CUSTOM_LIST:{list_id}", label=list_name, items=items))
        else:
            out.append(LinkedEntityOptionOut(key=key, label=labels.get(key, key), items=items))
    return out


@router.get("/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False,
    ).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403)
    return ticket


@router.get("/{ticket_id}/comments", response_model=list[TicketCommentOut])
def list_comments(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False,
    ).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403)
    return db.query(TicketComment).filter(TicketComment.ticket_id == ticket_id).order_by(TicketComment.created_at).all()


@router.post("", response_model=TicketOut)
def create_ticket(body: TicketCreateRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if user.role == "EMPLOYEE" and body.ticket_category != "HELP":
        raise HTTPException(status_code=403, detail="Employees can only create Help tickets")
    if user.role not in ("ADMIN", "MANAGER", "EMPLOYEE"):
        raise HTTPException(status_code=403)

    assignee_id = body.assignee_id or (user.id if user.role == "EMPLOYEE" else None)
    if not assignee_id:
        raise HTTPException(status_code=400, detail="assignee_id is required")

    ticket = Ticket(
        tenant_id=user.tenant_id, title=body.title, description=body.description,
        priority=body.priority, created_by_id=user.id, current_assignee_id=assignee_id,
        due_at=body.due_at, ticket_type="D",
        evidence_required=body.evidence_required, ticket_category=body.ticket_category,
    )
    db.add(ticket)
    db.flush()

    tenant = db.query(Tenant).get(user.tenant_id)
    tenant.ticket_seq = (tenant.ticket_seq or 0) + 1
    ticket.display_id = f"T-{tenant.ticket_seq:04d}"

    assignee = db.query(User).get(assignee_id)
    _log_event(db, ticket.id, user.id, "CREATED", f"Assigned to {assignee.name if assignee else assignee_id}")
    if assignee:
        notify_ticket_assigned(db, ticket, assignee)

    # P5-10: Linked Entities — any role can attach these at creation time,
    # same as the desktop create-ticket form (add/edit later is Admin/Manager only).
    for link in body.linked_entities:
        if link.entity_type.upper() == "OTHER" and not (link.custom_text or "").strip():
            continue
        if link.entity_type.upper() != "OTHER" and not link.entity_id:
            continue
        db.add(LinkedEntityReference(
            tenant_id=user.tenant_id, parent_type="TICKET", parent_id=ticket.id,
            entity_type=link.entity_type.upper(), entity_id=link.entity_id,
            entity_label=link.entity_label or link.entity_id or link.custom_text or "",
            custom_text=link.custom_text, created_by_id=user.id,
        ))

    db.commit()
    db.refresh(ticket)

    audience = list(set(_admin_ids(db, user.tenant_id) + _manager_ids_for_ticket(db, user.tenant_id, assignee_id) + [assignee_id]))
    broadcast_sync(user.tenant_id, audience, TICKET_ASSIGNED, {
        "ticket_id": ticket.id, "display_id": ticket.display_id,
        "title": ticket.title, "assignee_id": assignee_id,
    })
    return ticket


@router.patch("/{ticket_id}", response_model=TicketOut)
def update_ticket(ticket_id: str, body: TicketUpdateRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403)

    old_status = ticket.status
    for field in ("priority", "title", "description", "due_at"):
        value = getattr(body, field)
        if value is not None:
            setattr(ticket, field, value)

    if body.assignee_id is not None and body.assignee_id != ticket.current_assignee_id:
        can_reassign = user.role in ("ADMIN", "MANAGER") or ticket.current_assignee_id == user.id
        if not can_reassign:
            raise HTTPException(status_code=403, detail="Only the assignee, Admin, or Manager can reassign")
        new_assignee = db.query(User).filter(User.id == body.assignee_id, User.tenant_id == user.tenant_id).first()
        if not new_assignee:
            raise HTTPException(status_code=404, detail="Assignee not found")
        old_assignee_name = ticket.assignee_name
        ticket.current_assignee_id = body.assignee_id
        ticket.acknowledged_at = None
        _log_event(db, ticket.id, user.id, "REASSIGNED", f"{old_assignee_name or 'Unassigned'} -> {new_assignee.name}")
        notify_ticket_assigned(db, ticket, new_assignee)

    if body.status is not None and body.status != ticket.status:
        if body.status not in ("OPEN", "DONE", "CLOSED"):
            raise HTTPException(status_code=400, detail="Invalid status")
        if body.status == "CLOSED" and user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(status_code=403, detail="Only Admin or Manager can close tickets")
        if body.status == "OPEN" and ticket.status == "CLOSED" and user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="Only Admin can reopen a closed ticket")
        ticket.status = body.status
        if body.status == "CLOSED":
            ticket.closed_at = datetime.utcnow()
        _log_event(db, ticket.id, user.id, "STATUS_CHANGED", f"{old_status} -> {body.status}")

    db.commit()
    db.refresh(ticket)

    if body.status is not None and body.status != old_status:
        audience = list(set(_admin_ids(db, user.tenant_id) + ([ticket.current_assignee_id] if ticket.current_assignee_id else [])))
        broadcast_sync(user.tenant_id, audience, TICKET_STATUS_CHANGED, {
            "ticket_id": ticket.id, "display_id": ticket.display_id, "status": ticket.status,
        })
    return ticket


@router.get("/{ticket_id}/events", response_model=list[TicketEventOut])
def list_events(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False,
    ).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403)
    return db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket_id).order_by(TicketEvent.created_at).all()


@router.get("/{ticket_id}/linked-entities", response_model=list[LinkedEntityOut])
def get_ticket_linked_entities(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403)
    return db.query(LinkedEntityReference).filter(
        LinkedEntityReference.tenant_id == user.tenant_id,
        LinkedEntityReference.parent_type == "TICKET",
        LinkedEntityReference.parent_id == ticket_id,
    ).order_by(LinkedEntityReference.created_at).all()


@router.post("/{ticket_id}/linked-entities", response_model=LinkedEntityOut)
def add_ticket_linked_entity(ticket_id: str, body: LinkedEntityIn, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    # Matches the desktop detail-page "+ Add" panel — Admin/Manager only;
    # linking at ticket-creation time (any role) goes through create_ticket instead.
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    entity_type = body.entity_type.upper()
    if entity_type == "OTHER" and not (body.custom_text or "").strip():
        raise HTTPException(status_code=400, detail="custom_text is required for OTHER")
    if entity_type != "OTHER" and not body.entity_id:
        raise HTTPException(status_code=400, detail="entity_id is required")
    ref = LinkedEntityReference(
        tenant_id=user.tenant_id, parent_type="TICKET", parent_id=ticket_id,
        entity_type=entity_type, entity_id=body.entity_id,
        entity_label=body.entity_label or body.entity_id or body.custom_text or "",
        custom_text=body.custom_text, created_by_id=user.id,
    )
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return ref


@router.get("/{ticket_id}/attachments", response_model=list[AttachmentListOut])
def list_attachments(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403)
    return db.query(MediaUpload).filter(
        MediaUpload.tenant_id == user.tenant_id, MediaUpload.entity_type == "ticket", MediaUpload.entity_id == ticket_id,
    ).order_by(MediaUpload.created_at.desc()).all()


@router.get("/{ticket_id}/helpers", response_model=list[TicketHelperOut])
def list_helpers(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403)
    return db.query(TicketAssignee).filter(TicketAssignee.ticket_id == ticket_id).order_by(TicketAssignee.created_at).all()


@router.post("/{ticket_id}/helpers", response_model=TicketHelperOut)
def add_helper(ticket_id: str, body: HelperAddRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    existing = db.query(TicketAssignee).filter(TicketAssignee.ticket_id == ticket_id, TicketAssignee.user_id == body.user_id).first()
    if existing:
        return existing
    helper_user = db.query(User).filter(User.id == body.user_id, User.tenant_id == user.tenant_id).first()
    if not helper_user:
        raise HTTPException(status_code=404, detail="Employee not found")
    helper = TicketAssignee(ticket_id=ticket_id, user_id=body.user_id, added_by_id=user.id, note=body.note.strip())
    db.add(helper)
    _log_event(db, ticket_id, user.id, "HELPER_ADDED", f"Helper: {helper_user.name}")
    try:
        notify_helper_added(db, ticket, helper_user)
    except Exception:
        pass
    db.commit()
    db.refresh(helper)
    return helper


@router.delete("/{ticket_id}/helpers/{helper_user_id}", status_code=204)
def remove_helper(ticket_id: str, helper_user_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    removed_user = db.query(User).filter(User.id == helper_user_id).first()
    db.query(TicketAssignee).filter(TicketAssignee.ticket_id == ticket_id, TicketAssignee.user_id == helper_user_id).delete()
    _log_event(db, ticket_id, user.id, "HELPER_REMOVED", f"Helper: {removed_user.name if removed_user else helper_user_id}")
    db.commit()


@router.post("/{ticket_id}/acknowledge", response_model=TicketOut)
def acknowledge_ticket(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not (ticket.current_assignee_id == user.id or user.role in ("ADMIN", "MANAGER")):
        raise HTTPException(status_code=403)
    if ticket.status == "CLOSED":
        raise HTTPException(status_code=400, detail="Ticket is closed")
    if not ticket.acknowledged_at:
        ticket.acknowledged_at = datetime.utcnow()
        _log_event(db, ticket.id, user.id, "ACKNOWLEDGED")
        db.commit()
        db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/flag", response_model=TicketOut)
def flag_ticket(ticket_id: str, body: FlagRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403, detail="Only the current assignee can escalate")
    ticket.is_flagged = True
    ticket.flagged_reason = body.reason
    _log_event(db, ticket.id, user.id, "FLAGGED", body.reason)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/unflag", response_model=TicketOut)
def unflag_ticket(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket.is_flagged = False
    ticket.flagged_reason = None
    _log_event(db, ticket.id, user.id, "FLAG_REMOVED")
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/log-delay", response_model=TicketOut)
def log_delay(ticket_id: str, body: DelayRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403, detail="Only the current assignee can log a delay")
    if ticket.status != "OPEN":
        raise HTTPException(status_code=400, detail="Delays can only be logged on OPEN tickets")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required")
    _log_event(db, ticket_id, user.id, "DELAY_LOGGED", reason)
    admins = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for_ticket(db, user.tenant_id, ticket.current_assignee_id)
    try:
        notify_delay_logged(db, ticket, user.id, reason, admins, managers)
    except Exception:
        pass
    db.commit()
    db.refresh(ticket)
    return ticket


@router.delete("/{ticket_id}", status_code=204)
def delete_ticket(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if user.role != "ADMIN":
        raise HTTPException(status_code=403)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status != "OPEN":
        raise HTTPException(status_code=400, detail="Only open tickets can be deleted")
    ticket.is_deleted = True
    db.commit()


@router.post("/{ticket_id}/comments", response_model=TicketCommentOut)
def add_comment(ticket_id: str, body: CommentCreateRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
        raise HTTPException(status_code=403)

    comment = TicketComment(ticket_id=ticket_id, user_id=user.id, body=body.body)
    db.add(comment)
    _log_event(db, ticket.id, user.id, "COMMENTED", body.body[:200])
    db.commit()
    db.refresh(comment)

    audience = list(set(_admin_ids(db, user.tenant_id) + ([ticket.current_assignee_id] if ticket.current_assignee_id else []) + [ticket.created_by_id]))
    broadcast_sync(user.tenant_id, audience, TICKET_COMMENTED, {
        "ticket_id": ticket.id, "display_id": ticket.display_id, "body": body.body,
    })
    return comment


class AttachmentOut(BaseModel):
    file_name: str
    file_path: str
    file_type: str
    file_size: int


@router.post("/{ticket_id}/attachments", response_model=AttachmentOut)
@limiter.limit("30/minute")
async def upload_ticket_attachment(request: Request, ticket_id: str, file: UploadFile = File(...),
                                    user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Phase 0.5-B: JSON-friendly counterpart to the desktop /tickets/{id}/upload
    route. Same storage backend (save_upload -> local disk) and same
    MediaUpload record, just returns the stored file's reference instead of
    a redirect."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # save_upload validates by real file content (magic bytes), not the
    # client-declared Content-Type header — see security audit Part 4.
    info = await save_upload(file, user.tenant_id, allowed_kinds=("image",))
    db.add(MediaUpload(
        tenant_id=user.tenant_id, entity_type="ticket", entity_id=ticket_id,
        uploaded_by_id=user.id, **info,
    ))
    _log_event(db, ticket.id, user.id, "PROOF_UPLOADED", info["file_name"])
    db.commit()
    return info
