from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import User, get_db
from .pagination import paginate_cursor
from .schemas import Page
from .security import get_current_api_user, require_api_manager

router = APIRouter(prefix="/employees", tags=["Employees"])


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    phone: str
    email: Optional[str]
    role: str
    employee_id: Optional[str]
    department_id: Optional[str]
    branch_id: Optional[str]
    manager_id: Optional[str]
    status: str
    joining_date: Optional[date]
    created_at: datetime


@router.get("", response_model=Page[EmployeeOut])
def list_employees(
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(require_api_manager),
    db: Session = Depends(get_db),
):
    q = db.query(User).filter(User.tenant_id == user.tenant_id, User.is_deleted == False)
    rows, next_cursor = paginate_cursor(q, User, cursor, limit)
    return Page(items=rows, next_cursor=next_cursor)


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if user.role not in ("ADMIN", "MANAGER", "PRODUCT_MANAGER") and employee_id != user.id:
        raise HTTPException(status_code=403)
    emp = db.query(User).filter(User.id == employee_id, User.tenant_id == user.tenant_id, User.is_deleted == False).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return emp
