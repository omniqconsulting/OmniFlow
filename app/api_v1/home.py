from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..constants import get_tenant_enabled_tabs, has_feature
from ..database import (
    AttendanceRecord,
    ChecklistAssignment,
    Notification,
    Tenant,
    Ticket,
    TicketEvent,
    User,
    get_db,
)
from .security import get_current_api_user

router = APIRouter(tags=["Home"])

TICKET_EVENT_LABEL = {
    "CREATED": "Created",
    "STATUS_CHANGED": "Status Changed",
    "COMMENTED": "Commented",
    "PROOF_UPLOADED": "Evidence Uploaded",
}


def _rel_time(when: datetime) -> str:
    seconds = max(0, (datetime.utcnow() - when).total_seconds())
    if seconds < 60:
        return "Just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours // 24)
    return f"{days}d ago"


class ActivityItemOut(BaseModel):
    icon: str
    title: str
    meta: str
    rel: str
    cat: str


@router.get("/home/activity", response_model=list[ActivityItemOut])
def home_activity(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    can_manage = user.role in ("ADMIN", "MANAGER")

    ticket_events_q = (
        db.query(TicketEvent, Ticket)
        .join(Ticket, Ticket.id == TicketEvent.ticket_id)
        .filter(Ticket.tenant_id == user.tenant_id, Ticket.is_deleted == False)
    )
    if not can_manage:
        ticket_events_q = ticket_events_q.filter(
            (TicketEvent.actor_id == user.id) | (Ticket.current_assignee_id == user.id)
        )
    ticket_events = ticket_events_q.order_by(TicketEvent.created_at.desc()).limit(8).all()

    checklist_q = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == user.tenant_id,
        ChecklistAssignment.status == "DONE",
        ChecklistAssignment.is_deleted == False,
        ChecklistAssignment.completed_at.isnot(None),
    )
    if not can_manage:
        checklist_q = checklist_q.filter(ChecklistAssignment.user_id == user.id)
    checklists = checklist_q.order_by(ChecklistAssignment.completed_at.desc()).limit(8).all()

    items = []
    for event, ticket in ticket_events:
        actor = db.query(User).filter(User.id == event.actor_id).first()
        label = TICKET_EVENT_LABEL.get(event.event_type, event.event_type.replace("_", " ").title())
        items.append({
            "icon": "🎫",
            "title": f"{ticket.display_id or ticket.id} {ticket.title} — {label}",
            "meta": actor.name if actor else "Unknown",
            "rel": _rel_time(event.created_at),
            "cat": "op",
            "_when": event.created_at,
        })
    for assignment in checklists:
        completer = db.query(User).filter(User.id == assignment.user_id).first()
        items.append({
            "icon": "✅",
            "title": f"{assignment.template.title} completed",
            "meta": completer.name if completer else "Unknown",
            "rel": _rel_time(assignment.completed_at),
            "cat": "op",
            "_when": assignment.completed_at,
        })

    items.sort(key=lambda i: i["_when"], reverse=True)
    return [ActivityItemOut(**{k: v for k, v in item.items() if k != "_when"}) for item in items[:6]]


class AttendanceTodayOut(BaseModel):
    checked_in: bool
    checked_out: bool


class HomeSummary(BaseModel):
    role: str
    open_tickets: int
    open_checklists: int
    unread_notifications: int
    enabled_tabs: list[str]
    attendance_today: Optional[AttendanceTodayOut]


@router.get("/home", response_model=HomeSummary)
def home_summary(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    open_tickets = db.query(Ticket).filter(
        Ticket.tenant_id == user.tenant_id, Ticket.current_assignee_id == user.id,
        Ticket.status == "OPEN", Ticket.is_deleted == False,
    ).count()
    open_checklists = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == user.tenant_id, ChecklistAssignment.user_id == user.id,
        ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
        ChecklistAssignment.is_deleted == False,
    ).count()
    unread = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).count()
    # Same Setup > Access Control source of truth the website's nav uses —
    # lets the app hide a tile/tab instead of the user tapping into a 403.
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    enabled_tabs = get_tenant_enabled_tabs(tenant, db)

    attendance_today = None
    if has_feature(tenant, "ATTENDANCE", db):
        rec = db.query(AttendanceRecord).filter(
            AttendanceRecord.user_id == user.id, AttendanceRecord.work_date == date.today(),
        ).first()
        attendance_today = AttendanceTodayOut(
            checked_in=bool(rec and rec.check_in_at), checked_out=bool(rec and rec.check_out_at),
        )

    return HomeSummary(
        role=user.role, open_tickets=open_tickets, open_checklists=open_checklists,
        unread_notifications=unread, enabled_tabs=enabled_tabs, attendance_today=attendance_today,
    )
