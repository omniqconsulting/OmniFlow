"""
Knowledge Repository — per-tenant document / video / audio / link library.
Feature-gated: only accessible when KNOWLEDGE_REPO is enabled for the tenant.
"""
import math
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .database import get_db, KnowledgeItem, Tenant
from .auth import get_current_user, get_current_user_or_redirect, User, get_nav_flags
from .constants import has_feature
from .templates_env import templates
from .labels import get_labels, DEFAULT_L
from .database import Notification

router = APIRouter()

# ── helpers ──────────────────────────────────────────────────────────────────

def _L(db, user):
    if user is None:
        return DEFAULT_L
    return get_labels(db, user.tenant_id)

def _unread(db: Session, user: User) -> int:
    return db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read == False).count()

def _ctx(request, user, db, **kw):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first() if user else None
    return {
        "request": request, "user": user,
        "L": _L(db, user), "unread": _unread(db, user),
        **get_nav_flags(db, user, tenant),
        **kw,
    }

def _require_feature(user: User, db: Session):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant or not has_feature(tenant, "KNOWLEDGE_REPO", db):
        raise HTTPException(403, "Knowledge Repository is not enabled for your account.")
    return tenant


def _size_label(size_bytes: Optional[int]) -> str:
    if not size_bytes:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1024 ** 2:.1f} MB"


def _media_kind_from_mime(mime: str) -> str:
    mime = (mime or "").lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime in ("application/pdf",):
        return "document"
    if "word" in mime or "excel" in mime or "sheet" in mime or "presentation" in mime or "powerpoint" in mime:
        return "document"
    if mime.startswith("text/"):
        return "document"
    return "document"


# ── list / search ─────────────────────────────────────────────────────────────

PAGE_SIZE = 20

@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_index(
    request: Request,
    search: str = "",
    category: str = "",
    kind: str = "",
    page: int = 1,
    user: User = Depends(get_current_user_or_redirect),
    db: Session = Depends(get_db),
):
    _require_feature(user, db)

    q = db.query(KnowledgeItem).filter(
        KnowledgeItem.tenant_id == user.tenant_id,
        KnowledgeItem.is_deleted == False,
    )
    if search:
        like = f"%{search}%"
        q = q.filter(
            (KnowledgeItem.title.ilike(like)) |
            (KnowledgeItem.description.ilike(like)) |
            (KnowledgeItem.tags.ilike(like)) |
            (KnowledgeItem.category.ilike(like))
        )
    if category:
        q = q.filter(KnowledgeItem.category == category)
    if kind:
        q = q.filter(KnowledgeItem.media_kind == kind)

    total = q.count()
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    items = q.order_by(KnowledgeItem.created_at.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    # For the size labels
    for it in items:
        it._size_label = _size_label(it.file_size)

    # All categories for filter sidebar
    all_cats = db.query(KnowledgeItem.category).filter(
        KnowledgeItem.tenant_id == user.tenant_id,
        KnowledgeItem.is_deleted == False,
        KnowledgeItem.category != None,
        KnowledgeItem.category != "",
    ).distinct().all()
    categories = sorted(set(r[0] for r in all_cats if r[0]))

    template_name = "knowledge/index.html"

    return templates.TemplateResponse(request, template_name, _ctx(
        request, user, db,
        items=items,
        total=total,
        page=page,
        total_pages=total_pages,
        search=search,
        category=category,
        kind=kind,
        categories=categories,
        size_label=_size_label,
    ))


# ── upload (single) ───────────────────────────────────────────────────────────

@router.post("/knowledge/upload")
async def knowledge_upload(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    tags: str = Form(""),
    external_url: str = Form(""),
    file: Optional[UploadFile] = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature(user, db)

    file_url = file_name = file_type = None
    file_size = None
    media_kind = "link"

    if file and file.filename:
        from .uploads import save_upload
        result = await save_upload(file, user.tenant_id)
        file_url   = result["file_path"]
        file_name  = result["file_name"]
        file_type  = result["file_type"]
        file_size  = result["file_size"]
        media_kind = _media_kind_from_mime(file_type)
    elif external_url.strip():
        media_kind = "link"

    db.add(KnowledgeItem(
        tenant_id     = user.tenant_id,
        title         = title.strip(),
        description   = description.strip() or None,
        category      = category.strip() or None,
        tags          = tags.strip() or None,
        media_kind    = media_kind,
        file_url      = file_url,
        file_name     = file_name,
        file_type     = file_type,
        file_size     = file_size,
        external_url  = external_url.strip() or None,
        created_by_id = user.id,
        created_at    = datetime.utcnow(),
        updated_at    = datetime.utcnow(),
    ))
    db.commit()
    return RedirectResponse("/knowledge?msg=uploaded", status_code=303)


# ── bulk upload ───────────────────────────────────────────────────────────────

@router.post("/knowledge/bulk-upload")
async def knowledge_bulk_upload(
    request: Request,
    category: str = Form(""),
    tags: str = Form(""),
    files: List[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature(user, db)
    from .uploads import save_upload

    count = 0
    for file in files:
        if not file or not file.filename:
            continue
        try:
            result = await save_upload(file, user.tenant_id)
            media_kind = _media_kind_from_mime(result["file_type"])
            db.add(KnowledgeItem(
                tenant_id     = user.tenant_id,
                title         = result["file_name"],
                category      = category.strip() or None,
                tags          = tags.strip() or None,
                media_kind    = media_kind,
                file_url      = result["file_path"],
                file_name     = result["file_name"],
                file_type     = result["file_type"],
                file_size     = result["file_size"],
                created_by_id = user.id,
                created_at    = datetime.utcnow(),
                updated_at    = datetime.utcnow(),
            ))
            count += 1
        except Exception:
            pass

    db.commit()
    return RedirectResponse(f"/knowledge?msg=bulk_{count}", status_code=303)


# ── delete ────────────────────────────────────────────────────────────────────

@router.post("/knowledge/{item_id}/delete")
def knowledge_delete(
    item_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature(user, db)
    item = db.query(KnowledgeItem).filter(
        KnowledgeItem.id == item_id,
        KnowledgeItem.tenant_id == user.tenant_id,
    ).first()
    if not item:
        raise HTTPException(404)
    if user.role not in ("ADMIN", "MANAGER") and item.created_by_id != user.id:
        raise HTTPException(403)
    item.is_deleted = True
    db.commit()
    return RedirectResponse("/knowledge?msg=deleted", status_code=303)
