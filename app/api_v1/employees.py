import re
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from sqlalchemy.orm import Session
from sqlalchemy import func

from ..auth import hash_password
from ..constants import within_limit
from ..database import Tenant, User, get_db
from .features import require_feature
from .pagination import paginate_cursor
from .schemas import Page, UtcDateTime
from .security import get_current_api_user, require_api_manager

router = APIRouter(prefix="/employees", tags=["Employees"], dependencies=[Depends(require_feature("EMPLOYEES"))])


def _require_admin_or_pm(user: User = Depends(get_current_api_user)) -> User:
    if user.role not in ("ADMIN", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Admin or Product Manager only")
    return user


def _require_admin_or_pm_or_manager(user: User = Depends(get_current_api_user)) -> User:
    if user.role not in ("ADMIN", "MANAGER", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Admin, Manager or Product Manager only")
    return user


def _validate_phone(phone: str) -> Optional[str]:
    digits = re.sub(r"\D", "", phone)
    if len(digits) != 10:
        return "Phone must be exactly 10 digits"
    return None


def _validate_email(email: str) -> Optional[str]:
    if not email:
        return None
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return "Invalid email address format"
    return None


def _next_employee_id(db: Session, tenant_id: str) -> str:
    max_id = db.query(func.max(User.employee_id)).filter(
        User.tenant_id == tenant_id, User.employee_id.isnot(None)
    ).scalar()
    if max_id and max_id.startswith("EMP-"):
        try:
            return f"EMP-{int(max_id[4:]) + 1:04d}"
        except ValueError:
            pass
    return "EMP-0001"


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
    created_at: UtcDateTime


@router.get("", response_model=Page[EmployeeOut])
def list_employees(
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    my_team: bool = Query(False, description="Managers only: filter to direct reports (+ self)"),
    user: User = Depends(require_api_manager),
    db: Session = Depends(get_db),
):
    q = db.query(User).filter(User.tenant_id == user.tenant_id, User.is_deleted == False)
    if user.role == "MANAGER" and my_team:
        # Optional "My Team Only" filter — Managers see every employee by
        # default (edit/performance stay scoped to direct reports elsewhere).
        q = q.filter((User.manager_id == user.id) | (User.id == user.id))
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


class EmployeeCreateIn(BaseModel):
    name: str
    phone: str
    password: str
    role: str = "EMPLOYEE"
    email: Optional[str] = None
    department_id: Optional[str] = None
    manager_id: Optional[str] = None
    branch_id: Optional[str] = None


@router.post("", response_model=EmployeeOut)
def create_employee(payload: EmployeeCreateIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    phone_err = _validate_phone(payload.phone)
    if phone_err:
        raise HTTPException(status_code=422, detail=phone_err)
    email_err = _validate_email(payload.email or "")
    if email_err:
        raise HTTPException(status_code=422, detail=email_err)
    if len(payload.password) < 6:
        raise HTTPException(status_code=422, detail="Password must be at least 6 characters")
    if payload.role not in ("ADMIN", "MANAGER", "EMPLOYEE", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=422, detail="Invalid role")

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    current_count = db.query(User).filter(User.tenant_id == user.tenant_id, User.is_deleted == False).count()
    if not within_limit(tenant, "max_users", current_count):
        raise HTTPException(status_code=403, detail="Team member limit reached for your plan — upgrade to add more.")

    if db.query(User).filter(User.tenant_id == user.tenant_id, User.phone == payload.phone, User.is_deleted == False).first():
        raise HTTPException(status_code=409, detail="Phone already registered")

    emp = User(
        tenant_id=user.tenant_id, name=payload.name.strip(), phone=payload.phone,
        email=payload.email or None, password_hash=hash_password(payload.password), role=payload.role,
        department_id=payload.department_id or None, manager_id=payload.manager_id or None,
        branch_id=payload.branch_id or None, employee_id=_next_employee_id(db, user.tenant_id),
        status="ACTIVE",
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


class EmployeeUpdateIn(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    role: str
    department_id: Optional[str] = None
    manager_id: Optional[str] = None
    branch_id: Optional[str] = None
    joining_date: Optional[date] = None


@router.put("/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: str, payload: EmployeeUpdateIn, user: User = Depends(_require_admin_or_pm_or_manager), db: Session = Depends(get_db)):
    emp = db.query(User).filter(User.id == employee_id, User.tenant_id == user.tenant_id, User.is_deleted == False).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if user.role == "MANAGER" and emp.manager_id != user.id:
        raise HTTPException(status_code=403, detail="You can only edit your direct reports")
    phone_err = _validate_phone(payload.phone)
    if phone_err:
        raise HTTPException(status_code=422, detail=phone_err)
    email_err = _validate_email(payload.email or "")
    if email_err:
        raise HTTPException(status_code=422, detail=email_err)
    if payload.role not in ("ADMIN", "MANAGER", "EMPLOYEE", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=422, detail="Invalid role")
    if db.query(User).filter(
        User.tenant_id == user.tenant_id, User.phone == payload.phone,
        User.id != employee_id, User.is_deleted == False,
    ).first():
        raise HTTPException(status_code=409, detail="Phone already registered")

    emp.name = payload.name.strip()
    emp.phone = payload.phone
    emp.email = payload.email or None
    emp.role = payload.role
    emp.department_id = payload.department_id or None
    emp.manager_id = payload.manager_id or None
    emp.branch_id = payload.branch_id or None
    if payload.joining_date:
        emp.joining_date = payload.joining_date
    db.commit()
    db.refresh(emp)
    return emp


@router.delete("/{employee_id}", status_code=204)
def deactivate_employee(employee_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    emp = db.query(User).filter(User.id == employee_id, User.tenant_id == user.tenant_id, User.is_deleted == False).first()
    if emp:
        emp.is_deleted = True
        db.commit()
    return None
