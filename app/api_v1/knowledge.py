from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import KnowledgeItem, User, get_db
from .features import require_feature
from .pagination import paginate_cursor
from .schemas import Page, UtcDateTime
from .security import get_current_api_user

router = APIRouter(prefix="/knowledge", tags=["Knowledge"], dependencies=[Depends(require_feature("KNOWLEDGE_REPO"))])


class KnowledgeItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    description: Optional[str]
    category: Optional[str]
    media_kind: Optional[str]
    file_url: Optional[str]
    external_url: Optional[str]
    created_at: UtcDateTime


@router.get("", response_model=Page[KnowledgeItemOut])
def list_knowledge(
    category: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(KnowledgeItem).filter(KnowledgeItem.tenant_id == user.tenant_id, KnowledgeItem.is_deleted == False)
    if category:
        q = q.filter(KnowledgeItem.category == category)
    rows, next_cursor = paginate_cursor(q, KnowledgeItem, cursor, limit)
    return Page(items=rows, next_cursor=next_cursor)
