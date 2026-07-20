from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import FMSTicket, User, get_db
from .pagination import paginate_cursor
from .schemas import Page
from .security import get_current_api_user

router = APIRouter(prefix="/fms", tags=["FMS"])


class FMSTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    display_id: Optional[str]
    flow_id: str
    current_stage_id: Optional[str]
    title: str
    status: str
    priority: str
    current_assignee_id: Optional[str]
    due_at: Optional[datetime]
    is_flagged: bool
    created_at: datetime


@router.get("/tickets", response_model=Page[FMSTicketOut])
def list_fms_tickets(
    status: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(FMSTicket).filter(FMSTicket.tenant_id == user.tenant_id, FMSTicket.is_deleted == False)
    if status:
        q = q.filter(FMSTicket.status == status)
    if user.role == "EMPLOYEE":
        q = q.filter(FMSTicket.current_assignee_id == user.id)
    rows, next_cursor = paginate_cursor(q, FMSTicket, cursor, limit)
    return Page(items=rows, next_cursor=next_cursor)


@router.get("/tickets/{ticket_id}", response_model=FMSTicketOut)
def get_fms_ticket(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    t = db.query(FMSTicket).filter(FMSTicket.id == ticket_id, FMSTicket.tenant_id == user.tenant_id, FMSTicket.is_deleted == False).first()
    if not t:
        raise HTTPException(status_code=404, detail="FMS ticket not found")
    if user.role == "EMPLOYEE" and t.current_assignee_id != user.id:
        raise HTTPException(status_code=403)
    return t
