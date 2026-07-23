from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import PurchaseRequest, User, get_db
from .features import require_feature
from .pagination import paginate_cursor
from .schemas import Page, UtcDateTime
from .security import get_current_api_user

router = APIRouter(prefix="/inventory", tags=["Inventory"], dependencies=[Depends(require_feature("INVENTORY_MODULE"))])


class PurchaseRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    variant_id: str
    requested_by_id: str
    qty_requested: Optional[float]
    status: str
    created_at: UtcDateTime


@router.get("/po", response_model=Page[PurchaseRequestOut])
def list_purchase_requests(
    status: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(PurchaseRequest).filter(PurchaseRequest.tenant_id == user.tenant_id)
    if status:
        q = q.filter(PurchaseRequest.status == status)
    rows, next_cursor = paginate_cursor(q, PurchaseRequest, cursor, limit)
    return Page(items=rows, next_cursor=next_cursor)
