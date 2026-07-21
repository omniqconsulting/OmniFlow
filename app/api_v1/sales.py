from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import Customer, Product, SalesOrder, User, get_db
from .features import require_feature
from .pagination import paginate_cursor
from .schemas import Page
from .security import get_current_api_user

router = APIRouter(prefix="/sales", tags=["Sales"], dependencies=[Depends(require_feature("SALES_MODULE"))])


class SalesOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    display_id: Optional[str]
    customer_id: str
    agent_id: str
    status: str
    total_amount: float
    created_at: datetime


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    contact_person: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    customer_tier: str
    assigned_agent_id: Optional[str]
    created_at: datetime


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str]
    is_active: bool
    created_at: datetime


@router.get("/orders", response_model=Page[SalesOrderOut])
def list_orders(
    status: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(SalesOrder).filter(SalesOrder.tenant_id == user.tenant_id, SalesOrder.is_deleted == False)
    if status:
        q = q.filter(SalesOrder.status == status)
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(SalesOrder.agent_id == user.id)
    rows, next_cursor = paginate_cursor(q, SalesOrder, cursor, limit)
    return Page(items=rows, next_cursor=next_cursor)


@router.get("/orders/{order_id}", response_model=SalesOrderOut)
def get_order(order_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    o = db.query(SalesOrder).filter(SalesOrder.id == order_id, SalesOrder.tenant_id == user.tenant_id, SalesOrder.is_deleted == False).first()
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")
    if user.role not in ("ADMIN", "MANAGER") and o.agent_id != user.id:
        raise HTTPException(status_code=403)
    return o


@router.get("/contacts", response_model=Page[CustomerOut])
def list_contacts(
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(Customer).filter(Customer.tenant_id == user.tenant_id, Customer.is_deleted == False)
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(Customer.assigned_agent_id == user.id)
    rows, next_cursor = paginate_cursor(q, Customer, cursor, limit)
    return Page(items=rows, next_cursor=next_cursor)


@router.get("/catalog", response_model=Page[ProductOut])
def list_catalog(
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(Product).filter(Product.tenant_id == user.tenant_id, Product.is_deleted == False)
    rows, next_cursor = paginate_cursor(q, Product, cursor, limit)
    return Page(items=rows, next_cursor=next_cursor)
