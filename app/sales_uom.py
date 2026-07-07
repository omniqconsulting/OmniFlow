"""
Sales Foundation — Units of Measure (UOM) CRUD routes.
Admin-only setup routes under /setup/units.
"""
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .database import get_db, new_id, UnitOfMeasure
from .auth import require_admin, require_admin_or_redirect
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread

router = APIRouter()


@router.get("/setup/units", response_class=HTMLResponse)
def list_uoms(request: Request, user=Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
    uoms = (
        db.query(UnitOfMeasure)
        .filter(UnitOfMeasure.tenant_id == user.tenant_id, UnitOfMeasure.is_deleted == False)
        .order_by(UnitOfMeasure.name)
        .all()
    )
    ctx = {
        "request": request, "user": user, "uoms": uoms,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
        "L": _L(db, user), "unread": _unread(db, user),
    }
    ctx.update(_nav_ctx(db, user))
    return templates.TemplateResponse("setup/units.html", ctx)


@router.post("/setup/units/create")
async def create_uom(
    request: Request,
    name: str = Form(...),
    abbreviation: str = Form(...),
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    name = name.strip()
    abbreviation = abbreviation.strip()
    if not name or not abbreviation:
        return RedirectResponse("/setup/units?err=Name+and+abbreviation+are+required", status_code=303)
    existing = db.query(UnitOfMeasure).filter(
        UnitOfMeasure.tenant_id == user.tenant_id,
        UnitOfMeasure.name == name,
        UnitOfMeasure.is_deleted == False,
    ).first()
    if existing:
        return RedirectResponse(f"/setup/units?err=A+UOM+named+'{name}'+already+exists", status_code=303)
    db.add(UnitOfMeasure(id=new_id(), tenant_id=user.tenant_id, name=name, abbreviation=abbreviation))
    db.commit()
    return RedirectResponse("/setup/units?msg=Unit+of+measure+added", status_code=303)


@router.post("/setup/units/{unit_id}/edit")
async def edit_uom(
    unit_id: str,
    name: str = Form(...),
    abbreviation: str = Form(...),
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    uom = db.query(UnitOfMeasure).filter(
        UnitOfMeasure.id == unit_id,
        UnitOfMeasure.tenant_id == user.tenant_id,
        UnitOfMeasure.is_deleted == False,
    ).first()
    if not uom:
        raise HTTPException(404, "UOM not found")
    uom.name = name.strip()
    uom.abbreviation = abbreviation.strip()
    db.commit()
    return RedirectResponse("/setup/units?msg=Unit+of+measure+updated", status_code=303)


@router.post("/setup/units/{unit_id}/toggle")
async def toggle_uom(
    unit_id: str,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    uom = db.query(UnitOfMeasure).filter(
        UnitOfMeasure.id == unit_id,
        UnitOfMeasure.tenant_id == user.tenant_id,
        UnitOfMeasure.is_deleted == False,
    ).first()
    if not uom:
        raise HTTPException(404, "UOM not found")
    uom.is_active = not uom.is_active
    db.commit()
    status = "activated" if uom.is_active else "deactivated"
    return RedirectResponse(f"/setup/units?msg=UOM+{status}", status_code=303)
