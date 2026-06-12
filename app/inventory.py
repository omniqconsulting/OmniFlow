"""
Phase 4 — Inventory Management  (4-A through 4-D)
4-A  Store Manager role + separate dashboard
4-B  Material catalogue (full CRUD)
4-C  Stock movements  (Stock In / Stock Out / Adjustment) — immutable ledger
4-D  Purchase Orders  (Draft → Approve → Receive → auto-update stock)

All labels read from L dict — fully domain agnostic.
Access gated by INVENTORY feature flag (SA enables per tenant).
"""
import json as _json
from datetime import datetime, date
from typing import Optional

import csv, io as _io
from fastapi import APIRouter, Depends, File, Form, Request, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import (
    get_db, new_id,
    Tenant, User, Branch, Department,
    Material, StockMovement, PurchaseOrder, PurchaseOrderItem,
    MaterialRequest, Notification,
)
from .auth import get_current_user, require_store_manager, require_inventory_admin
from .labels import get_labels, DEFAULT_L
from .constants import has_feature
from .ws_manager import broadcast_sync, STORE_ALERT

import os
BASE_DIR  = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

router = APIRouter(prefix="/inventory", tags=["Inventory"])

MOVEMENT_TYPES = ["STOCK_IN", "STOCK_OUT", "ADJUSTMENT", "PO_RECEIPT", "OPENING", "RETURN"]
PO_STATUSES    = ["DRAFT", "SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _r(path: str):
    return RedirectResponse(path, status_code=302)

def _require_inv(db, user):
    """Raise 403 if INVENTORY feature not enabled for this tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "INVENTORY", db):
        raise HTTPException(403,
            "Inventory Management is not enabled for your account. "
            "Contact your Super Admin to enable it.")

def _unread(db, user):
    return db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False).count()

def _ctx(request, user, db, **kw):
    L = get_labels(db, user.tenant_id)
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return {
        "request": request, "user": user, "L": L,
        "unread": _unread(db, user),
        "has_inventory": True,   # always true inside inventory routes
        **kw,
    }

def _get_material(db, material_id, tenant_id) -> Material:
    m = db.query(Material).filter(
        Material.id == material_id,
        Material.tenant_id == tenant_id,
        Material.is_deleted == False,
    ).first()
    if not m:
        raise HTTPException(404, "Material not found")
    return m

def _get_po(db, po_id, tenant_id) -> PurchaseOrder:
    po = db.query(PurchaseOrder).filter(
        PurchaseOrder.id == po_id,
        PurchaseOrder.tenant_id == tenant_id,
        PurchaseOrder.is_deleted == False,
    ).first()
    if not po:
        raise HTTPException(404, "Purchase Order not found")
    return po

def _apply_movement(db, material: Material, movement_type: str,
                    qty: float, unit_cost: Optional[float],
                    actor_id: str, tenant_id: str,
                    reference: str = "", notes: str = "",
                    po_item_id: Optional[str] = None,
                    branch_id: Optional[str] = None,
                    dept_id: Optional[str] = None) -> StockMovement:
    """Apply a stock movement and update material.current_stock atomically."""
    qty_before = material.current_stock or 0
    if movement_type in ("STOCK_IN", "PO_RECEIPT", "OPENING", "RETURN"):
        qty_after = qty_before + qty
    elif movement_type in ("STOCK_OUT",):
        qty_after = qty_before - qty
    elif movement_type == "ADJUSTMENT":
        qty_after = qty   # qty is the new absolute value for adjustments
        qty       = abs(qty_after - qty_before)
    else:
        qty_after = qty_before + qty

    material.current_stock = qty_after
    material.updated_at    = datetime.utcnow()

    mv = StockMovement(
        tenant_id=tenant_id, material_id=material.id,
        branch_id=branch_id, department_id=dept_id,
        movement_type=movement_type,
        qty=qty, qty_before=qty_before, qty_after=qty_after,
        unit=material.unit,
        unit_cost=unit_cost,
        total_cost=(unit_cost * qty) if unit_cost else None,
        reference=reference, notes=notes,
        po_item_id=po_item_id, actor_id=actor_id,
    )
    db.add(mv)
    return mv

def _po_auto_number(db, tenant_id: str) -> str:
    """Generate PO-YYYY-NNNN style number."""
    year = datetime.utcnow().year
    count = db.query(func.count(PurchaseOrder.id)).filter(
        PurchaseOrder.tenant_id == tenant_id).scalar() or 0
    return f"PO-{year}-{count+1:04d}"

def _notify_low_stock(db, material: Material, tenant_id: str, actor_id: str):
    """Create reorder alert notification if stock hits reorder threshold."""
    if (material.reorder_threshold or 0) > 0 and \
       material.current_stock <= material.reorder_threshold:
        managers = db.query(User).filter(
            User.tenant_id == tenant_id,
            User.role.in_(["ADMIN", "MANAGER", "STORE_MANAGER"]),
            User.is_deleted == False,
        ).all()
        for u in managers:
            db.add(Notification(
                tenant_id=tenant_id, user_id=u.id,
                notif_type="TICKET_FLAGGED",
                title=f"⚠ Low stock: {material.name}",
                body=(f"{material.name} has dropped to {material.current_stock} {material.unit} "
                      f"(reorder point: {material.reorder_threshold})."),
                link=f"/inventory/materials/{material.id}",
            ))
        broadcast_sync(tenant_id, [u.id for u in managers], STORE_ALERT, {
            "material_id": material.id,
            "material_name": material.name,
            "current_stock": material.current_stock,
            "reorder_threshold": material.reorder_threshold,
        })


# ══════════════════════════════════════════════════════════════════════════════
# 4-A: Dashboard — Store Manager landing page
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def inventory_dashboard(request: Request,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """4-A: Inventory dashboard — completely separate from ticket world."""
    _require_inv(db, user)

    tid = user.tenant_id

    # ── KPI strip ─────────────────────────────────────────────────────────────
    total_materials = db.query(func.count(Material.id)).filter(
        Material.tenant_id == tid, Material.is_deleted == False,
        Material.is_active == True).scalar() or 0

    low_stock = db.query(func.count(Material.id)).filter(
        Material.tenant_id == tid, Material.is_deleted == False,
        Material.is_active == True,
        Material.reorder_threshold > 0,
        Material.current_stock <= Material.reorder_threshold,
    ).scalar() or 0

    total_pos = db.query(func.count(PurchaseOrder.id)).filter(
        PurchaseOrder.tenant_id == tid, PurchaseOrder.is_deleted == False,
    ).scalar() or 0

    open_pos = db.query(func.count(PurchaseOrder.id)).filter(
        PurchaseOrder.tenant_id == tid, PurchaseOrder.is_deleted == False,
        PurchaseOrder.status.in_(["SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]),
    ).scalar() or 0

    pending_requests = db.query(func.count(MaterialRequest.id)).filter(
        MaterialRequest.tenant_id == tid,
        MaterialRequest.status == "PENDING",
    ).scalar() or 0

    # ── Recent movements (last 10) ─────────────────────────────────────────────
    recent_movements = db.query(StockMovement).filter(
        StockMovement.tenant_id == tid,
    ).order_by(StockMovement.created_at.desc()).limit(10).all()

    # ── Low stock materials ────────────────────────────────────────────────────
    low_stock_items = db.query(Material).filter(
        Material.tenant_id == tid, Material.is_deleted == False,
        Material.is_active == True,
        Material.reorder_threshold > 0,
        Material.current_stock <= Material.reorder_threshold,
    ).order_by(Material.current_stock.asc()).limit(8).all()

    # ── Draft / open POs ──────────────────────────────────────────────────────
    open_po_list = db.query(PurchaseOrder).filter(
        PurchaseOrder.tenant_id == tid, PurchaseOrder.is_deleted == False,
        PurchaseOrder.status.in_(["DRAFT", "SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]),
    ).order_by(PurchaseOrder.created_at.desc()).limit(5).all()

    return templates.TemplateResponse(request, "inventory/dashboard.html", _ctx(
        request, user, db,
        total_materials=total_materials, low_stock=low_stock,
        total_pos=total_pos, open_pos=open_pos,
        pending_requests=pending_requests,
        recent_movements=recent_movements,
        low_stock_items=low_stock_items,
        open_po_list=open_po_list,
    ))


# ══════════════════════════════════════════════════════════════════════════════
# 4-B: Material Catalogue
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/materials/template")
def materials_csv_template(user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """6-A: Download a CSV template for bulk material import."""
    _require_inv(db, user)
    rows = [
        "name,unit,description,reorder_threshold,reorder_qty,lead_time_days,supplier,opening_stock,unit_cost",
        "Steel Rods 10mm,kg,Hot rolled steel,100,500,7,Tata Steel,1000,85.50",
    ]
    content = "\n".join(rows)
    return StreamingResponse(
        _io.BytesIO(content.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=materials_import_template.csv"},
    )


@router.post("/materials/import")
async def materials_bulk_import(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """6-B: Bulk import materials from CSV."""
    _require_inv(db, user)
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "BULK_IMPORT", db):
        raise HTTPException(403, "Bulk import requires Professional plan or above")

    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(_io.StringIO(content))

    errors, imported = [], 0
    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        if not name:
            continue  # silently skip blank rows

        # Duplicate check (case-sensitive within tenant)
        existing = db.query(Material).filter(
            Material.tenant_id == user.tenant_id,
            Material.name == name,
            Material.is_deleted == False,
        ).first()
        if existing:
            errors.append({"row": i, "error": "duplicate name", "name": name})
            continue

        def _int(val, default=0):
            try: return int(float(val)) if str(val).strip() else default
            except: return default

        def _float(val, default=0.0):
            try: return float(val) if str(val).strip() else default
            except: return default

        opening_stock = _float(row.get("opening_stock", 0))
        mat = Material(
            tenant_id=user.tenant_id,
            name=name,
            unit=(row.get("unit") or "pcs").strip(),
            description=(row.get("description") or "").strip(),
            reorder_threshold=_float(row.get("reorder_threshold", 0)),
            reorder_qty=_float(row.get("reorder_qty", 0)),
            lead_time_days=_int(row.get("lead_time_days", 0)),
            supplier=(row.get("supplier") or "").strip(),
            current_stock=0,
        )
        db.add(mat)
        db.flush()

        if opening_stock > 0:
            unit_cost = _float(row.get("unit_cost", 0)) or None
            _apply_movement(
                db, mat, "OPENING", opening_stock,
                unit_cost=unit_cost, actor_id=user.id,
                tenant_id=user.tenant_id,
                reference="Bulk import opening stock",
            )

        imported += 1

    db.commit()

    if errors:
        error_csv = "row,error,name\n" + "\n".join(
            f"{e['row']},{e['error']},{e['name']}" for e in errors
        )
        return StreamingResponse(
            _io.BytesIO(error_csv.encode()),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=materials_import_errors.csv",
                "X-Imported": str(imported),
            },
        )

    return _r(f"/inventory/materials?msg=imported_{imported}")


@router.get("/materials", response_class=HTMLResponse)
def materials_list(request: Request,
                   q: str = Query(""),
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """4-B: Full material catalogue with search."""
    _require_inv(db, user)
    query = db.query(Material).filter(
        Material.tenant_id == user.tenant_id,
        Material.is_deleted == False,
    )
    if q:
        query = query.filter(Material.name.ilike(f"%{q}%"))
    materials = query.order_by(Material.name).all()
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return templates.TemplateResponse(request, "inventory/materials.html", _ctx(
        request, user, db, materials=materials, q=q,
        can_bulk=has_feature(tenant, "BULK_IMPORT", db),
        msg=request.query_params.get("msg", ""),
    ))


@router.post("/materials/add")
def material_add(
    name: str = Form(...),
    unit: str = Form("pcs"),
    description: str = Form(""),
    category: str = Form(""),
    reorder_threshold: float = Form(0),
    reorder_qty: float = Form(0),
    lead_time_days: int = Form(0),
    supplier: str = Form(""),
    opening_stock: float = Form(0),
    unit_cost: float = Form(0),
    user: User = Depends(require_store_manager),
    db: Session = Depends(get_db),
):
    """4-B: Add a new material to the catalogue."""
    _require_inv(db, user)
    m = Material(
        tenant_id=user.tenant_id, name=name.strip(), unit=unit.strip() or "pcs",
        description=description or None,
        reorder_threshold=reorder_threshold, reorder_qty=reorder_qty,
        lead_time_days=lead_time_days,
        supplier=supplier or None,
        opening_stock=opening_stock, current_stock=opening_stock,
        created_by_id=user.id,
    )
    db.add(m)
    db.flush()
    # Record opening stock movement if > 0
    if opening_stock > 0:
        _apply_movement(
            db, m, "OPENING", opening_stock,
            unit_cost=unit_cost if unit_cost else None,
            actor_id=user.id, tenant_id=user.tenant_id,
            notes="Opening stock",
        )
    db.commit()
    return _r(f"/inventory/materials?msg=added")


@router.get("/materials/{material_id}", response_class=HTMLResponse)
def material_detail(material_id: str, request: Request,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """4-B: Material detail page with full movement history."""
    _require_inv(db, user)
    material = _get_material(db, material_id, user.tenant_id)
    movements = db.query(StockMovement).filter(
        StockMovement.material_id == material_id,
    ).order_by(StockMovement.created_at.desc()).all()
    branches = db.query(Branch).filter(
        Branch.tenant_id == user.tenant_id,
        Branch.is_deleted == False,
    ).all()
    return templates.TemplateResponse(request, "inventory/material_detail.html", _ctx(
        request, user, db, material=material, movements=movements,
        branches=branches,
        msg=request.query_params.get("msg", ""),
    ))


@router.post("/materials/{material_id}/edit")
def material_edit(
    material_id: str,
    name: str = Form(...),
    unit: str = Form("pcs"),
    description: str = Form(""),
    reorder_threshold: float = Form(0),
    reorder_qty: float = Form(0),
    lead_time_days: int = Form(0),
    supplier: str = Form(""),
    is_active: bool = Form(True),
    user: User = Depends(require_store_manager),
    db: Session = Depends(get_db),
):
    _require_inv(db, user)
    m = _get_material(db, material_id, user.tenant_id)
    m.name = name.strip()
    m.unit = unit.strip() or "pcs"
    m.description = description or None
    m.reorder_threshold = reorder_threshold
    m.reorder_qty = reorder_qty
    m.lead_time_days = lead_time_days
    m.supplier = supplier or None
    m.is_active = is_active
    m.updated_at = datetime.utcnow()
    db.commit()
    return _r(f"/inventory/materials/{material_id}?msg=saved")


@router.post("/materials/{material_id}/delete")
def material_delete(material_id: str,
                    user: User = Depends(require_inventory_admin),
                    db: Session = Depends(get_db)):
    _require_inv(db, user)
    m = _get_material(db, material_id, user.tenant_id)
    m.is_deleted = True
    m.updated_at = datetime.utcnow()
    db.commit()
    return _r("/inventory/materials?msg=deleted")


# ══════════════════════════════════════════════════════════════════════════════
# 4-C: Stock Movements — immutable ledger
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/movements", response_class=HTMLResponse)
def movements_list(request: Request,
                   material_id: str = Query(""),
                   movement_type: str = Query(""),
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """4-C: Full stock movement ledger with filters."""
    _require_inv(db, user)
    query = db.query(StockMovement).filter(
        StockMovement.tenant_id == user.tenant_id,
    )
    if material_id:
        query = query.filter(StockMovement.material_id == material_id)
    if movement_type:
        query = query.filter(StockMovement.movement_type == movement_type)
    movements = query.order_by(StockMovement.created_at.desc()).limit(200).all()

    materials = db.query(Material).filter(
        Material.tenant_id == user.tenant_id,
        Material.is_deleted == False,
        Material.is_active == True,
    ).order_by(Material.name).all()

    return templates.TemplateResponse(request, "inventory/movements.html", _ctx(
        request, user, db, movements=movements,
        materials=materials, movement_types=MOVEMENT_TYPES,
        filter_material_id=material_id, filter_type=movement_type,
    ))


@router.post("/movements/add")
def movement_add(
    material_id: str = Form(...),
    movement_type: str = Form(...),
    qty: float = Form(...),
    unit_cost: float = Form(0),
    reference: str = Form(""),
    notes: str = Form(""),
    branch_id: str = Form(""),
    user: User = Depends(require_store_manager),
    db: Session = Depends(get_db),
):
    """4-C: Record a manual stock movement."""
    _require_inv(db, user)
    if movement_type not in MOVEMENT_TYPES:
        raise HTTPException(400, "Invalid movement type")
    if qty <= 0:
        raise HTTPException(400, "Quantity must be positive")

    material = _get_material(db, material_id, user.tenant_id)

    _apply_movement(
        db, material, movement_type, qty,
        unit_cost=unit_cost if unit_cost else None,
        actor_id=user.id, tenant_id=user.tenant_id,
        reference=reference.strip(), notes=notes.strip(),
        branch_id=branch_id or None,
    )
    _notify_low_stock(db, material, user.tenant_id, user.id)
    db.commit()
    return _r(f"/inventory/materials/{material_id}?msg=movement_added")


# ══════════════════════════════════════════════════════════════════════════════
# 4-D: Purchase Orders
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/purchase-orders", response_class=HTMLResponse)
def po_list(request: Request,
            status: str = Query(""),
            user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    """4-D: Purchase order list."""
    _require_inv(db, user)
    query = db.query(PurchaseOrder).filter(
        PurchaseOrder.tenant_id == user.tenant_id,
        PurchaseOrder.is_deleted == False,
    )
    if status:
        query = query.filter(PurchaseOrder.status == status)
    pos = query.order_by(PurchaseOrder.created_at.desc()).all()
    return templates.TemplateResponse(request, "inventory/po_list.html", _ctx(
        request, user, db, pos=pos,
        po_statuses=PO_STATUSES, filter_status=status,
        msg=request.query_params.get("msg", ""),
    ))


@router.get("/purchase-orders/new", response_class=HTMLResponse)
def po_new_page(request: Request,
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _require_inv(db, user)
    materials_orm = db.query(Material).filter(
        Material.tenant_id == user.tenant_id,
        Material.is_deleted == False, Material.is_active == True,
    ).order_by(Material.name).all()
    # Pass plain dicts for tojson serialization in JavaScript
    import json as _j
    materials_json = _j.dumps([
        {"id": m.id, "name": m.name, "unit": m.unit or "pcs"}
        for m in materials_orm
    ])
    return templates.TemplateResponse(request, "inventory/po_new.html", _ctx(
        request, user, db, materials=materials_orm, materials_json=materials_json,
    ))


@router.post("/purchase-orders/new")
def po_create(
    supplier: str = Form(""),
    supplier_ref: str = Form(""),
    expected_delivery: str = Form(""),
    notes: str = Form(""),
    items_json: str = Form("[]"),
    user: User = Depends(require_store_manager),
    db: Session = Depends(get_db),
):
    """4-D: Create a new Purchase Order with line items."""
    _require_inv(db, user)
    try:
        items_data = _json.loads(items_json)
    except Exception:
        raise HTTPException(400, "Invalid items JSON")
    if not items_data:
        raise HTTPException(400, "At least one line item is required")

    po = PurchaseOrder(
        tenant_id=user.tenant_id,
        po_number=_po_auto_number(db, user.tenant_id),
        supplier=supplier.strip() or None,
        supplier_ref=supplier_ref.strip() or None,
        expected_delivery=date.fromisoformat(expected_delivery) if expected_delivery.strip() else None,
        notes=notes.strip() or None,
        status="DRAFT",
        created_by_id=user.id,
    )
    db.add(po)
    db.flush()

    total = 0.0
    for itm in items_data:
        mat_id   = itm.get("material_id", "")
        mat_name = itm.get("material_name", "").strip()
        unit     = itm.get("unit", "pcs")
        try:
            qty      = float(itm.get("qty_ordered", 0))
            ucost    = float(itm.get("unit_cost", 0) or 0)
        except (ValueError, TypeError):
            continue
        if qty <= 0:
            continue
        # Resolve material name from catalogue if id provided
        if mat_id:
            mat = db.query(Material).filter(
                Material.id == mat_id,
                Material.tenant_id == user.tenant_id,
            ).first()
            if mat:
                mat_name = mat.name
                unit     = mat.unit

        line_total = qty * ucost if ucost else None
        if line_total:
            total += line_total
        db.add(PurchaseOrderItem(
            po_id=po.id, tenant_id=user.tenant_id,
            material_id=mat_id or None,
            material_name=mat_name or "Unknown",
            unit=unit, qty_ordered=qty,
            unit_cost=ucost or None,
            total_cost=line_total,
        ))

    po.total_amount = total
    db.commit()
    return _r(f"/inventory/purchase-orders/{po.id}?msg=created")


@router.get("/purchase-orders/{po_id}", response_class=HTMLResponse)
def po_detail(po_id: str, request: Request,
              user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """4-D: PO detail with receive form."""
    _require_inv(db, user)
    po = _get_po(db, po_id, user.tenant_id)
    return templates.TemplateResponse(request, "inventory/po_detail.html", _ctx(
        request, user, db, po=po,
        msg=request.query_params.get("msg", ""),
    ))


@router.post("/purchase-orders/{po_id}/submit")
def po_submit(po_id: str,
              user: User = Depends(require_store_manager),
              db: Session = Depends(get_db)):
    """4-D: Submit PO for approval (DRAFT → SUBMITTED)."""
    _require_inv(db, user)
    po = _get_po(db, po_id, user.tenant_id)
    if po.status != "DRAFT":
        raise HTTPException(400, "Only DRAFT orders can be submitted")
    po.status = "SUBMITTED"
    po.updated_at = datetime.utcnow()
    db.commit()
    return _r(f"/inventory/purchase-orders/{po_id}?msg=submitted")


@router.post("/purchase-orders/{po_id}/approve")
def po_approve(po_id: str,
               user: User = Depends(require_inventory_admin),
               db: Session = Depends(get_db)):
    """4-D: Approve PO (SUBMITTED → APPROVED). Admin only."""
    _require_inv(db, user)
    po = _get_po(db, po_id, user.tenant_id)
    if po.status != "SUBMITTED":
        raise HTTPException(400, "Only SUBMITTED orders can be approved")
    po.status = "APPROVED"
    po.approved_by_id = user.id
    po.approved_at = datetime.utcnow()
    po.updated_at  = datetime.utcnow()
    # Notify Store Managers
    sms = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.role == "STORE_MANAGER", User.is_deleted == False,
    ).all()
    for sm in sms:
        db.add(Notification(
            tenant_id=user.tenant_id, user_id=sm.id,
            notif_type="TICKET_STATUS_CHANGED",
            title=f"✅ PO Approved: {po.po_number}",
            body=f"Purchase Order {po.po_number} has been approved. You can now receive stock.",
            link=f"/inventory/purchase-orders/{po.id}",
        ))
    db.commit()
    return _r(f"/inventory/purchase-orders/{po_id}?msg=approved")


@router.post("/purchase-orders/{po_id}/cancel")
def po_cancel(po_id: str, cancel_reason: str = Form(""),
              user: User = Depends(require_inventory_admin),
              db: Session = Depends(get_db)):
    """4-D: Cancel a PO (any status except RECEIVED)."""
    _require_inv(db, user)
    po = _get_po(db, po_id, user.tenant_id)
    if po.status == "RECEIVED":
        raise HTTPException(400, "Cannot cancel a fully received order")
    po.status = "CANCELLED"
    po.cancel_reason = cancel_reason.strip() or None
    po.cancelled_at  = datetime.utcnow()
    po.updated_at    = datetime.utcnow()
    db.commit()
    return _r(f"/inventory/purchase-orders/{po_id}?msg=cancelled")


@router.post("/purchase-orders/{po_id}/receive")
def po_receive(
    po_id: str,
    received_quantities: str = Form("{}"),   # JSON: {item_id: qty_received}
    notes: str = Form(""),
    user: User = Depends(require_store_manager),
    db: Session = Depends(get_db),
):
    """4-D: Receive stock against a PO — updates stock ledger automatically."""
    _require_inv(db, user)
    po = _get_po(db, po_id, user.tenant_id)
    if po.status not in ("APPROVED", "PARTIALLY_RECEIVED"):
        raise HTTPException(400, "PO must be APPROVED or PARTIALLY_RECEIVED to receive stock")

    try:
        received = _json.loads(received_quantities)
    except Exception:
        raise HTTPException(400, "Invalid received quantities format")

    any_received = False
    for item in po.items:
        qty_in = float(received.get(item.id, 0) or 0)
        if qty_in <= 0:
            continue
        any_received = True
        item.qty_received = (item.qty_received or 0) + qty_in
        if item.qty_received >= item.qty_ordered:
            item.is_fully_received = True

        if item.material_id:
            material = db.query(Material).filter(
                Material.id == item.material_id,
                Material.tenant_id == user.tenant_id,
            ).first()
            if material:
                _apply_movement(
                    db, material, "PO_RECEIPT", qty_in,
                    unit_cost=item.unit_cost,
                    actor_id=user.id, tenant_id=user.tenant_id,
                    reference=po.po_number,
                    notes=notes.strip() or f"Received against {po.po_number}",
                    po_item_id=item.id,
                )
                _notify_low_stock(db, material, user.tenant_id, user.id)

    if not any_received:
        raise HTTPException(400, "No quantities entered")

    # Update PO status
    all_received = all(i.is_fully_received for i in po.items)
    po.status = "RECEIVED" if all_received else "PARTIALLY_RECEIVED"
    if all_received:
        po.received_at = datetime.utcnow()
    po.updated_at = datetime.utcnow()
    db.commit()
    return _r(f"/inventory/purchase-orders/{po_id}?msg=received")


# ── Pending material requests from FMS ────────────────────────────────────────

@router.get("/requests/new", response_class=HTMLResponse)
def standalone_request_new(request: Request,
                           user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """Standalone material request — not linked to any FMS ticket."""
    _require_inv(db, user)
    catalogue = db.query(Material).filter(
        Material.tenant_id == user.tenant_id,
        Material.is_active == True,
        Material.is_deleted == False,
    ).order_by(Material.name).all()
    return templates.TemplateResponse(request, "inventory/standalone_request.html", _ctx(
        request, user, db,
        catalogue=catalogue,
        msg=request.query_params.get("msg", ""),
    ))


@router.post("/requests/new")
def standalone_request_create(
    material_id: str = Form(""),
    material_name: str = Form(""),
    qty_requested: int = Form(...),
    unit: str = Form(""),
    reason: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a standalone material request (ticket_id = NULL)."""
    _require_inv(db, user)
    if not material_name.strip() and not material_id.strip():
        return _r("/inventory/requests/new?msg=error_no_material")

    mat_name = material_name.strip()
    mat_unit = unit.strip()
    if material_id.strip():
        mat = db.query(Material).filter(
            Material.id == material_id.strip(),
            Material.tenant_id == user.tenant_id,
        ).first()
        if mat:
            mat_name = mat_name or mat.name
            mat_unit = mat_unit or mat.unit

    req = MaterialRequest(
        ticket_id=None,
        tenant_id=user.tenant_id,
        material_id=material_id.strip() or None,
        material_name=mat_name,
        qty_requested=qty_requested,
        unit=mat_unit or "",
        reason=reason.strip() or None,
        requested_by_id=user.id,
    )
    db.add(req)
    db.commit()

    # Notify ADMIN + MANAGER + STORE_MANAGER
    notif_users = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.role.in_(["ADMIN", "MANAGER", "STORE_MANAGER"]),
        User.is_deleted == False,
    ).all()
    for u in notif_users:
        db.add(Notification(
            tenant_id=user.tenant_id, user_id=u.id,
            notif_type="TICKET_FLAGGED",
            title=f"📦 Standalone material request: {mat_name}",
            body=f"{user.name} requested {qty_requested} {mat_unit} of {mat_name}.",
            link="/inventory/requests",
        ))
    db.commit()
    broadcast_sync(user.tenant_id, [u.id for u in notif_users], STORE_ALERT, {
        "event": "MATERIAL_REQUEST",
        "material": mat_name,
        "qty": qty_requested,
    })
    return _r("/inventory/requests/new?msg=submitted")


@router.get("/requests", response_class=HTMLResponse)
def pending_requests(request: Request,
                     status_filter: str = Query(""),
                     user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Show material requests — filterable by status."""
    _require_inv(db, user)
    q = db.query(MaterialRequest).filter(
        MaterialRequest.tenant_id == user.tenant_id,
    )
    if status_filter:
        q = q.filter(MaterialRequest.status == status_filter)
    else:
        q = q.filter(MaterialRequest.status.in_(["PENDING", "APPROVED"]))
    reqs = q.order_by(MaterialRequest.created_at.desc()).all()
    return templates.TemplateResponse(request, "inventory/requests.html", _ctx(
        request, user, db, reqs=reqs,
        status_filter=status_filter,
        msg=request.query_params.get("msg", ""),
    ))


@router.post("/requests/{req_id}/fulfil")
def request_fulfil(req_id: str,
                   qty_fulfilled: float = Form(...),
                   notes: str = Form(""),
                   user: User = Depends(require_store_manager),
                   db: Session = Depends(get_db)):
    """Fulfil an approved material request — deducts from stock."""
    _require_inv(db, user)
    req = db.query(MaterialRequest).filter(
        MaterialRequest.id == req_id,
        MaterialRequest.tenant_id == user.tenant_id,
    ).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != "APPROVED":
        raise HTTPException(400, "Only APPROVED requests can be fulfilled")
    if req.material_id:
        material = db.query(Material).filter(
            Material.id == req.material_id,
            Material.tenant_id == user.tenant_id,
        ).first()
        if material:
            if material.current_stock < qty_fulfilled:
                raise HTTPException(400,
                    f"Insufficient stock: {material.current_stock} {material.unit} available")
            _apply_movement(
                db, material, "STOCK_OUT", qty_fulfilled,
                unit_cost=None, actor_id=user.id, tenant_id=user.tenant_id,
                reference=f"REQ-{req_id[:8]}",
                notes=notes or f"Fulfilled material request",
            )
            _notify_low_stock(db, material, user.tenant_id, user.id)
    req.fulfilled_qty = qty_fulfilled
    req.fulfilled_at  = datetime.utcnow()
    req.status = "FULFILLED"
    db.commit()
    return _r(f"/inventory/requests?msg=fulfilled")
