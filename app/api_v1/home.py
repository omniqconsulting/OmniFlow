from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import ChecklistAssignment, Notification, Ticket, User, get_db
from .security import get_current_api_user

router = APIRouter(tags=["Home"])


class HomeSummary(BaseModel):
    role: str
    open_tickets: int
    open_checklists: int
    unread_notifications: int


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
    return HomeSummary(role=user.role, open_tickets=open_tickets, open_checklists=open_checklists, unread_notifications=unread)
