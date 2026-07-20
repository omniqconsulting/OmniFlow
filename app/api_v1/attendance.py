from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import AttendanceRecord, LeaveRequest, User, get_db
from .pagination import paginate_cursor
from .schemas import Page
from .security import get_current_api_user

router = APIRouter(prefix="/attendance", tags=["Attendance"])


class AttendanceRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    work_date: date
    branch_id: Optional[str]
    check_in_at: Optional[datetime]
    check_out_at: Optional[datetime]
    is_half_day: bool


class LeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    leave_type: str
    start_date: date
    end_date: date
    is_half_day: bool
    status: str
    created_at: datetime


@router.get("/records", response_model=Page[AttendanceRecordOut])
def list_attendance_records(
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(AttendanceRecord).filter(AttendanceRecord.tenant_id == user.tenant_id)
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(AttendanceRecord.user_id == user.id)
    rows, next_cursor = paginate_cursor(q, AttendanceRecord, cursor, limit, created_col="created_at")
    return Page(items=rows, next_cursor=next_cursor)


@router.get("/leave", response_model=Page[LeaveRequestOut])
def list_leave_requests(
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(LeaveRequest).filter(LeaveRequest.tenant_id == user.tenant_id)
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(LeaveRequest.user_id == user.id)
    rows, next_cursor = paginate_cursor(q, LeaveRequest, cursor, limit, created_col="created_at")
    return Page(items=rows, next_cursor=next_cursor)
