"""
Sales Inventory — Brief 03: Inventory & Godown.
Stock snapshot, stock ledger, stock-in, purchase orders, godown dashboard.
Operates on ProductVariant (the sellable SKU) — see Catalog Hierarchy Phase 1.
"""
import csv
import io
import json
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Optional

from .database import (
    get_db, new_id, Product, ProductVariant, UnitOfMeasure, User, Vendor,
    ProductStock, StockLedgerEntry, InventoryPurchaseOrder, InventoryPOItem,
    StockReservation,
)
from .auth import get_current_user, require_manager, has_module, require_module
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread
from .constants import BULK_IMPORT_MAX_ROWS
from .bulk_common import check_required_headers

router = APIRouter()

_require_inventory = require_module("INVENTORY", "INVENTORY_MODULE")


def _require_inventory_manager(user: User = Depends(_require_inventory)) -> User:
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    return user


def _ctx(db: Session, user: User, **extra) -> dict:
    ctx = {
        "user": user, "L": _L(db, user), "unread": _unread(db, user),
    }
    ctx.update(_nav_ctx(db, user))
    ctx.update(extra)
    return ctx


def _variant_unit_abbr(variant: ProductVariant) -> str:
    unit = variant.base_unit or (variant.product.base_unit if variant.product else None)
    return unit.abbreviation if unit else "units"


# ══════════════════════════════════════════════════════════════════════════════
# BUSINESS LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def stock_status_badge(stock: ProductStock, variant: ProductVariant):
    if stock.qty_available <= 0:
        return ("OUT", "red")
    if variant.low_stock_threshold and stock.qty_available < variant.low_stock_threshold:
        return ("LOW", "amber")
    return ("OK", "green")


def handle_stock_in(db: Session, variant_id: str, qty: float, unit_cost: Optional[float],
                     vendor_name: Optional[str], notes: Optional[str], actor_id: str, tenant_id: str,
                     reference_type: str = "MANUAL", reference_id: str = None):
    """
    Record physical stock arriving at the godown.
    Updates product_stock and writes a ledger entry in the same transaction.
    """
    stock = (
        db.query(ProductStock)
        .filter(ProductStock.variant_id == variant_id, ProductStock.tenant_id == tenant_id)
        .with_for_update()
        .first()
    )
    if not stock:
        raise ValueError("Variant stock record not found. Ensure variant exists.")

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    was_below_threshold = (
        variant.low_stock_threshold is not None and
        stock.qty_available < variant.low_stock_threshold
    )

    if unit_cost and stock.avg_cost is not None:
        total_qty = stock.qty_available + qty
        stock.avg_cost = (
            (stock.qty_available * stock.avg_cost + qty * unit_cost) / total_qty
            if total_qty > 0 else unit_cost
        )
    elif unit_cost:
        stock.avg_cost = unit_cost

    stock.qty_available += qty
    stock.last_updated_at = datetime.utcnow()

    db.add(StockLedgerEntry(
        tenant_id=tenant_id,
        variant_id=variant_id,
        movement_type="STOCK_IN",
        qty=qty,
        unit_cost=unit_cost,
        reference_type=reference_type,
        reference_id=reference_id,
        notes=notes or (f"Vendor: {vendor_name}" if vendor_name else None),
        actor_id=actor_id,
    ))

    db.commit()

    _notify_stock_updated(db, variant_id, tenant_id, qty, stock.qty_available)

    if was_below_threshold and variant.low_stock_threshold and stock.qty_available >= variant.low_stock_threshold:
        pass  # resolved — no dedicated "resolved" template defined yet

    _check_low_stock_alert(db, variant_id, tenant_id)


def handle_stock_adjustment(db: Session, variant_id: str, new_qty: float,
                             reason: str, actor_id: str, tenant_id: str):
    """Admin/Manager sets stock to a specific quantity (correction after physical count)."""
    stock = (
        db.query(ProductStock)
        .filter(ProductStock.variant_id == variant_id, ProductStock.tenant_id == tenant_id)
        .with_for_update()
        .first()
    )
    if not stock:
        raise ValueError("Variant stock record not found.")

    delta = new_qty - stock.qty_available
    stock.qty_available = new_qty
    stock.last_updated_at = datetime.utcnow()

    db.add(StockLedgerEntry(
        tenant_id=tenant_id,
        variant_id=variant_id,
        movement_type="ADJUSTMENT",
        qty=delta,
        reference_type="MANUAL",
        notes=reason,
        actor_id=actor_id,
    ))
    db.commit()

    _check_low_stock_alert(db, variant_id, tenant_id)


def handle_po_receive(db: Session, po: InventoryPurchaseOrder, received_items: list, actor_id: str, tenant_id: str):
    """
    received_items: [{"po_item_id": "...", "qty_received": 50.0, "unit_cost": 120.0}]
    """
    for recv in received_items:
        po_item = next((i for i in po.items if i.id == recv["po_item_id"]), None)
        if not po_item:
            continue
        qty = recv["qty_received"]
        if qty <= 0:
            continue
        po_item.qty_received += qty

        handle_stock_in(
            db, po_item.variant_id, qty,
            unit_cost=recv.get("unit_cost") or po_item.unit_cost,
            vendor_name=po.vendor_name_snapshot,
            notes=f"PO receipt: {po.display_id}",
            actor_id=actor_id,
            tenant_id=tenant_id,
            reference_type="PO",
            reference_id=po.id,
        )

        stock = db.query(ProductStock).filter(ProductStock.variant_id == po_item.variant_id).first()
        if stock:
            stock.qty_in_transit = max(0, stock.qty_in_transit - qty)

    all_received = all(i.qty_received >= i.qty_ordered for i in po.items)
    po.status = "RECEIVED" if all_received else "PARTIALLY_RECEIVED"
    po.updated_at = datetime.utcnow()
    db.commit()


def _apply_in_transit_delta(db: Session, po: InventoryPurchaseOrder, sign: int):
    """sign=+1 when PO becomes SUBMITTED/APPROVED, sign=-1 when cancelled."""
    for item in po.items:
        stock = (
            db.query(ProductStock)
            .filter(ProductStock.variant_id == item.variant_id)
            .with_for_update()
            .first()
        )
        if stock:
            remaining = item.qty_ordered - item.qty_received
            stock.qty_in_transit = max(0, stock.qty_in_transit + sign * remaining)
    db.commit()


# ── Notifications ──────────────────────────────────────────────────────────

def _notify_stock_updated(db: Session, variant_id: str, tenant_id: str, qty_added: float, new_available: float):
    from .notifications import create_notification
    from .constants import WHATSAPP_TEMPLATES

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    product_name = f"{variant.product.name} ({variant.sku_code})" if variant.product else variant.sku_code
    managers = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.role.in_(["ADMIN", "MANAGER"]),
        User.is_deleted == False,
        User.is_active == True,
    ).all()

    unit_abbr = _variant_unit_abbr(variant)
    for mgr in managers:
        create_notification(
            db=db, tenant_id=tenant_id, user_id=mgr.id,
            notif_type="STOCK_UPDATED",
            title=f"Stock updated: {product_name}",
            body=f"+{qty_added} {unit_abbr}. Now available: {new_available}",
            link="/inventory-v2/stock",
        )
    db.commit()

    template = WHATSAPP_TEMPLATES.get("omniflow_stock_updated", {})
    if template.get("msg91_template_id"):
        from .services.msg91 import send_whatsapp_template
        from .database import WhatsAppMessageLog
        for mgr in managers:
            if not mgr.mobile_verified:
                continue
            variables = [mgr.name, product_name, str(qty_added), str(new_available)]
            success, error = send_whatsapp_template(mgr.phone, "omniflow_stock_updated", variables)
            db.add(WhatsAppMessageLog(
                tenant_id=tenant_id, template_name="omniflow_stock_updated",
                recipient_user_id=mgr.id, recipient_phone=mgr.phone,
                variables_json=json.dumps(variables),
                status="SENT" if success else "FAILED", error_message=error,
                related_entity_type="product_stock", related_entity_id=variant_id,
            ))
        db.commit()


def _check_low_stock_alert(db: Session, variant_id: str, tenant_id: str):
    from .notifications import create_notification
    from .constants import WHATSAPP_TEMPLATES

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    stock = db.query(ProductStock).filter(ProductStock.variant_id == variant_id).first()
    if not variant or not stock or not variant.low_stock_threshold:
        return
    if stock.qty_available >= variant.low_stock_threshold:
        return

    product_name = f"{variant.product.name} ({variant.sku_code})" if variant.product else variant.sku_code
    managers = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.role.in_(["ADMIN", "MANAGER"]),
        User.is_deleted == False,
        User.is_active == True,
    ).all()

    for mgr in managers:
        create_notification(
            db=db, tenant_id=tenant_id, user_id=mgr.id,
            notif_type="LOW_STOCK_ALERT",
            title=f"Low stock: {product_name}",
            body=f"Available: {stock.qty_available} (threshold: {variant.low_stock_threshold})",
            link="/inventory-v2/stock",
        )
    db.commit()

    template = WHATSAPP_TEMPLATES.get("omniflow_low_stock_alert", {})
    if template.get("msg91_template_id"):
        from .services.msg91 import send_whatsapp_template
        from .database import WhatsAppMessageLog
        for mgr in managers:
            if not mgr.mobile_verified:
                continue
            variables = [mgr.name, product_name, str(stock.qty_available), str(variant.low_stock_threshold)]
            success, error = send_whatsapp_template(mgr.phone, "omniflow_low_stock_alert", variables)
            db.add(WhatsAppMessageLog(
                tenant_id=tenant_id, template_name="omniflow_low_stock_alert",
                recipient_user_id=mgr.id, recipient_phone=mgr.phone,
                variables_json=json.dumps(variables),
                status="SENT" if success else "FAILED", error_message=error,
                related_entity_type="product_stock", related_entity_id=variant_id,
            ))
        db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/inventory-v2", response_class=HTMLResponse)
def inventory_dashboard(request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    tier_order = {"A": 0, "B": 1, "C": 2, "D": 3, "UNRANKED": 4}
    variants = db.query(ProductVariant).filter(
        ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False,
    ).all()
    stocks_by_variant = {
        s.variant_id: s for s in db.query(ProductStock).filter(ProductStock.tenant_id == user.tenant_id).all()
    }

    rows = []
    for v in variants:
        stock = stocks_by_variant.get(v.id)
        if not stock:
            continue
        badge_label, badge_color = stock_status_badge(stock, v)
        sort_key = (
            0 if stock.qty_available <= 0 else
            1 if (v.low_stock_threshold and stock.qty_available < v.low_stock_threshold) else 2,
            tier_order.get(v.product_tier, 4),
            v.product.name if v.product else v.sku_code,
        )
        rows.append((sort_key, v, stock, badge_label, badge_color))
    rows.sort(key=lambda r: r[0])
    stock_rows = [(v, s, lbl, color) for _, v, s, lbl, color in rows]

    open_pos = db.query(InventoryPurchaseOrder).filter(
        InventoryPurchaseOrder.tenant_id == user.tenant_id,
        InventoryPurchaseOrder.status.in_(["SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]),
        InventoryPurchaseOrder.is_deleted == False,
    ).order_by(InventoryPurchaseOrder.expected_arrival_date.asc().nullslast()).all()

    upcoming_dispatches = get_upcoming_dispatches(db, user.tenant_id)
    demand_projection = get_demand_projection(db, user.tenant_id)

    return templates.TemplateResponse(request, "inventory_v2/dashboard.html", _ctx(
        db, user,
        stock_rows=stock_rows,
        open_pos=open_pos,
        upcoming_dispatches=upcoming_dispatches,
        demand_projection=demand_projection,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


# ══════════════════════════════════════════════════════════════════════════════
# STOCK LIST / ADJUST / EXPORT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/inventory-v2/stock", response_class=HTMLResponse)
def stock_list(request: Request, q: str = "", user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    query = db.query(ProductVariant).join(Product, ProductVariant.product_id == Product.id).filter(
        ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False,
    )
    if q:
        like = f"%{q}%"
        query = query.filter((Product.name.ilike(like)) | (ProductVariant.sku_code.ilike(like)))
    variants = query.order_by(Product.name).all()
    stocks_by_variant = {
        s.variant_id: s for s in db.query(ProductStock).filter(ProductStock.tenant_id == user.tenant_id).all()
    }
    rows = []
    for v in variants:
        stock = stocks_by_variant.get(v.id)
        if not stock:
            continue
        badge_label, badge_color = stock_status_badge(stock, v)
        rows.append((v, stock, badge_label, badge_color))

    can_edit = user.role in ("ADMIN", "MANAGER")
    return templates.TemplateResponse(request, "inventory_v2/stock_list.html", _ctx(
        db, user, rows=rows, q=q, can_edit=can_edit,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/inventory-v2/stock/{variant_id}/adjust")
def stock_adjust_submit(
    variant_id: str,
    new_qty: float = Form(...),
    reason: str = Form(...),
    user: User = Depends(_require_inventory_manager),
    db: Session = Depends(get_db),
):
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id, ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False,
    ).first()
    if not variant:
        raise HTTPException(404, "Variant not found")
    try:
        handle_stock_adjustment(db, variant_id, new_qty, reason, user.id, user.tenant_id)
    except ValueError as e:
        return RedirectResponse(f"/inventory-v2/stock?err={e}", status_code=303)
    return RedirectResponse("/inventory-v2/stock?msg=Stock+adjusted", status_code=303)


@router.get("/inventory-v2/stock/export")
def stock_export(user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    variants = db.query(ProductVariant).join(Product, ProductVariant.product_id == Product.id).filter(
        ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False,
    ).order_by(Product.name).all()
    stocks_by_variant = {
        s.variant_id: s for s in db.query(ProductStock).filter(ProductStock.tenant_id == user.tenant_id).all()
    }

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "sku_code", "product_name", "variant_label", "unit", "qty_available", "qty_reserved",
        "qty_in_transit", "avg_cost", "low_stock_threshold", "status", "last_updated_at",
    ])
    for v in variants:
        stock = stocks_by_variant.get(v.id)
        if not stock:
            continue
        badge_label, _ = stock_status_badge(stock, v)
        unit = v.base_unit or (v.product.base_unit if v.product else None)
        writer.writerow([
            v.sku_code, v.product.name if v.product else "", v.variant_label or "",
            unit.abbreviation if unit else "",
            stock.qty_available, stock.qty_reserved, stock.qty_in_transit,
            stock.avg_cost or "", v.low_stock_threshold or "",
            badge_label, stock.last_updated_at.isoformat() if stock.last_updated_at else "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=stock_export.csv"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# STOCK-IN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/inventory-v2/stock-in/new", response_class=HTMLResponse)
def stock_in_new(request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    variants = db.query(ProductVariant).join(Product, ProductVariant.product_id == Product.id).filter(
        ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False, ProductVariant.is_active == True,
    ).order_by(Product.name).all()
    units = db.query(UnitOfMeasure).filter(
        UnitOfMeasure.tenant_id == user.tenant_id, UnitOfMeasure.is_active == True,
    ).order_by(UnitOfMeasure.name).all()
    return templates.TemplateResponse(request, "inventory_v2/stock_in_new.html", _ctx(
        db, user, variants=variants, units=units,
        err=request.query_params.get("err", ""),
    ))


@router.post("/inventory-v2/stock-in/create")
async def stock_in_create(
    request: Request,
    variant_id: str = Form(...),
    qty: float = Form(...),
    unit_cost: str = Form(""),
    vendor_name: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(_require_inventory),
    db: Session = Depends(get_db),
):
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id, ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False,
    ).first()
    if not variant:
        return RedirectResponse("/inventory-v2/stock-in/new?err=Variant+not+found", status_code=303)
    if qty <= 0:
        return RedirectResponse("/inventory-v2/stock-in/new?err=Quantity+must+be+positive", status_code=303)

    bill_photo_path = None
    form = await request.form()
    bill_photo = form.get("bill_photo")
    if bill_photo is not None and getattr(bill_photo, "filename", ""):
        content = await bill_photo.read()
        if content:
            ext = bill_photo.filename.rsplit(".", 1)[-1].lower()
            from pathlib import Path
            import uuid as _uuid
            filename = f"{_uuid.uuid4().hex}.{ext}"
            rel_path = f"uploads/{user.tenant_id}/stock_in_bills/{filename}"
            full_path = Path(__file__).parent / "static" / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(content)
            bill_photo_path = rel_path

    try:
        handle_stock_in(
            db, variant_id, qty,
            unit_cost=float(unit_cost) if unit_cost else None,
            vendor_name=vendor_name.strip() or None,
            notes=(notes.strip() or "") + (f" [Bill: {bill_photo_path}]" if bill_photo_path else ""),
            actor_id=user.id, tenant_id=user.tenant_id,
        )
    except ValueError as e:
        return RedirectResponse(f"/inventory-v2/stock-in/new?err={e}", status_code=303)

    return RedirectResponse("/inventory-v2/stock?msg=Stock-in+recorded", status_code=303)


@router.get("/inventory-v2/stock-in/bulk-template")
def stock_in_bulk_template(user: User = Depends(_require_inventory)):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["sku_code", "qty", "unit_abbreviation", "unit_cost", "vendor_name", "date", "notes"])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=stock_in_template.csv"},
    )


def _validate_stock_in_row(row: dict, tenant_id: str, db: Session) -> List[str]:
    errors = []
    sku = (row.get("sku_code") or "").strip()
    if not sku:
        errors.append("sku_code is required")
    else:
        variant = db.query(ProductVariant).filter(
            ProductVariant.tenant_id == tenant_id, ProductVariant.sku_code == sku, ProductVariant.is_deleted == False,
        ).first()
        if not variant:
            errors.append(f"Unknown sku_code: {sku}")

    qty_raw = (row.get("qty") or "").strip()
    try:
        qty = float(qty_raw)
        if qty <= 0:
            errors.append("qty must be a positive number")
    except (TypeError, ValueError):
        errors.append("qty must be a positive number")

    unit_abbr = (row.get("unit_abbreviation") or "").strip()
    if unit_abbr:
        unit = db.query(UnitOfMeasure).filter(
            UnitOfMeasure.tenant_id == tenant_id, UnitOfMeasure.abbreviation == unit_abbr,
            UnitOfMeasure.is_active == True,
        ).first()
        if not unit:
            errors.append(f"Unknown unit_abbreviation: {unit_abbr}")

    date_raw = (row.get("date") or "").strip()
    if date_raw:
        try:
            datetime.strptime(date_raw, "%Y-%m-%d")
        except ValueError:
            errors.append("date must be in YYYY-MM-DD format")

    return errors


_STOCK_IN_COLS = ["sku_code", "qty", "unit_abbreviation", "unit_cost", "vendor_name", "date", "notes"]


@router.get("/inventory-v2/stock-in/bulk", response_class=HTMLResponse)
def stock_in_bulk_page(request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "inventory_v2/stock_in_bulk.html", _ctx(db, user, columns=_STOCK_IN_COLS))


def _run_stock_in_validation(rows_in: list, tenant_id: str, db: Session, start_index: int = 2) -> dict:
    results = []
    valid_rows = []
    for i, row in enumerate(rows_in, start=start_index):
        errors = _validate_stock_in_row(row, tenant_id, db)
        if not errors:
            valid_rows.append(row)
        else:
            results.append({"row": row.get("_row", i), "error": "; ".join(errors), "data": dict(row)})
    return {
        "total": len(valid_rows) + len(results),
        "valid": len(valid_rows),
        "errors": results,
        "rows": valid_rows,
    }


@router.post("/inventory-v2/stock-in/bulk-upload")
async def stock_in_bulk_upload(
    file: UploadFile = File(...),
    user: User = Depends(_require_inventory),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    if (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload the CSV template, not an Excel file.")
    content = raw.decode("utf-8-sig", errors="replace").lstrip(chr(65279))
    try:
        dict_reader = csv.DictReader(io.StringIO(content))
        rows = list(dict_reader)
    except csv.Error:
        raise HTTPException(400, "Could not parse file — please upload a valid CSV using the provided template.")
    fmt_err = check_required_headers(dict_reader.fieldnames, ["sku_code", "qty"], _STOCK_IN_COLS)
    if fmt_err:
        return JSONResponse({"format_error": fmt_err})
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(rows)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    for i, row in enumerate(rows, start=2):
        row["_row"] = i
    return JSONResponse(_run_stock_in_validation(rows, user.tenant_id, db))


@router.post("/inventory-v2/stock-in/bulk-upload/revalidate")
async def stock_in_bulk_revalidate(request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    body = await request.json()
    rows_in = body.get("rows", [])
    if len(rows_in) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Too many rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    return JSONResponse(_run_stock_in_validation(rows_in, user.tenant_id, db))


@router.post("/inventory-v2/stock-in/bulk-upload/confirm")
async def stock_in_bulk_confirm(request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    body = await request.json()
    rows = body.get("rows", [])

    created = 0
    skipped = 0
    for row in rows:
        errors = _validate_stock_in_row(row, user.tenant_id, db)
        if errors:
            skipped += 1
            continue
        sku = row.get("sku_code", "").strip()
        variant = db.query(ProductVariant).filter(
            ProductVariant.tenant_id == user.tenant_id, ProductVariant.sku_code == sku, ProductVariant.is_deleted == False,
        ).first()
        try:
            handle_stock_in(
                db, variant.id, float(row["qty"]),
                unit_cost=float(row["unit_cost"]) if (row.get("unit_cost") or "").strip() else None,
                vendor_name=(row.get("vendor_name") or "").strip() or None,
                notes=(row.get("notes") or "").strip() or None,
                actor_id=user.id, tenant_id=user.tenant_id,
            )
            created += 1
        except ValueError:
            skipped += 1

    return JSONResponse({"created": created, "skipped": skipped})


# ══════════════════════════════════════════════════════════════════════════════
# PURCHASE ORDERS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/inventory-v2/purchase-orders", response_class=HTMLResponse)
def po_list(request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    pos = db.query(InventoryPurchaseOrder).filter(
        InventoryPurchaseOrder.tenant_id == user.tenant_id, InventoryPurchaseOrder.is_deleted == False,
    ).order_by(InventoryPurchaseOrder.created_at.desc()).all()
    return templates.TemplateResponse(request, "inventory_v2/po_list.html", _ctx(
        db, user, pos=pos,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.get("/inventory-v2/purchase-orders/new", response_class=HTMLResponse)
def po_new_form(request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    variants = db.query(ProductVariant).join(Product, ProductVariant.product_id == Product.id).filter(
        ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False, ProductVariant.is_active == True,
    ).order_by(Product.name).all()
    vendors = db.query(Vendor).filter(
        Vendor.tenant_id == user.tenant_id, Vendor.is_deleted == False, Vendor.is_active == True,
    ).order_by(Vendor.name).all()
    units = db.query(UnitOfMeasure).filter(
        UnitOfMeasure.tenant_id == user.tenant_id, UnitOfMeasure.is_active == True,
    ).order_by(UnitOfMeasure.name).all()
    return templates.TemplateResponse(request, "inventory_v2/po_new.html", _ctx(
        db, user, variants=variants, vendors=vendors, units=units,
        err=request.query_params.get("err", ""),
    ))


@router.post("/inventory-v2/purchase-orders/create")
async def po_create(
    request: Request,
    vendor_id: str = Form(""),
    vendor_name: str = Form(""),
    expected_arrival_date: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(_require_inventory),
    db: Session = Depends(get_db),
):
    form = await request.form()
    variant_ids = form.getlist("variant_id[]")
    qtys = form.getlist("qty_ordered[]")
    unit_costs = form.getlist("unit_cost[]")
    unit_ids = form.getlist("unit_id[]")

    if not variant_ids:
        return RedirectResponse("/inventory-v2/purchase-orders/new?err=Add+at+least+one+line+item", status_code=303)

    vendor_name_snapshot = vendor_name.strip() or None
    if vendor_id:
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.tenant_id == user.tenant_id).first()
        if vendor:
            vendor_name_snapshot = vendor.name

    po_count = db.query(InventoryPurchaseOrder).filter(InventoryPurchaseOrder.tenant_id == user.tenant_id).count()
    po = InventoryPurchaseOrder(
        id=new_id(),
        tenant_id=user.tenant_id,
        display_id=f"PO-{po_count + 1:04d}",
        vendor_id=vendor_id or None,
        vendor_name_snapshot=vendor_name_snapshot,
        status="DRAFT",
        expected_arrival_date=datetime.strptime(expected_arrival_date, "%Y-%m-%d").date() if expected_arrival_date else None,
        notes=notes.strip() or None,
        created_by_id=user.id,
    )
    db.add(po)
    db.flush()

    for vid, qty_raw, cost_raw, uid in zip(variant_ids, qtys, unit_costs, unit_ids):
        if not vid or not qty_raw:
            continue
        try:
            qty = float(qty_raw)
        except ValueError:
            continue
        if qty <= 0:
            continue
        db.add(InventoryPOItem(
            id=new_id(), po_id=po.id, variant_id=vid, qty_ordered=qty,
            unit_cost=float(cost_raw) if cost_raw else None,
            unit_id=uid or None,
        ))

    db.commit()
    return RedirectResponse(f"/inventory-v2/purchase-orders/{po.id}?msg=PO+created", status_code=303)


def _get_po_or_404(db: Session, po_id: str, tenant_id: str) -> InventoryPurchaseOrder:
    po = db.query(InventoryPurchaseOrder).filter(
        InventoryPurchaseOrder.id == po_id, InventoryPurchaseOrder.tenant_id == tenant_id,
        InventoryPurchaseOrder.is_deleted == False,
    ).first()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    return po


@router.get("/inventory-v2/purchase-orders/{po_id}", response_class=HTMLResponse)
def po_detail(po_id: str, request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    po = _get_po_or_404(db, po_id, user.tenant_id)
    return templates.TemplateResponse(request, "inventory_v2/po_detail.html", _ctx(
        db, user, po=po,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/inventory-v2/purchase-orders/{po_id}/submit")
def po_submit(po_id: str, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    po = _get_po_or_404(db, po_id, user.tenant_id)
    if po.status != "DRAFT":
        return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?err=PO+is+not+in+DRAFT+status", status_code=303)
    if not po.items:
        return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?err=PO+has+no+line+items", status_code=303)
    po.status = "SUBMITTED"
    po.updated_at = datetime.utcnow()
    db.commit()
    _apply_in_transit_delta(db, po, +1)
    return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?msg=PO+submitted", status_code=303)


@router.post("/inventory-v2/purchase-orders/{po_id}/approve")
def po_approve(po_id: str, user: User = Depends(_require_inventory_manager), db: Session = Depends(get_db)):
    po = _get_po_or_404(db, po_id, user.tenant_id)
    if po.status != "SUBMITTED":
        return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?err=PO+is+not+in+SUBMITTED+status", status_code=303)
    po.status = "APPROVED"
    po.approved_by_id = user.id
    po.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?msg=PO+approved", status_code=303)


@router.post("/inventory-v2/purchase-orders/{po_id}/receive")
async def po_receive(po_id: str, request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    po = _get_po_or_404(db, po_id, user.tenant_id)
    if po.status not in ("SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"):
        return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?err=PO+cannot+be+received+in+current+status", status_code=303)

    form = await request.form()
    item_ids = form.getlist("po_item_id[]")
    qtys = form.getlist("qty_received[]")
    costs = form.getlist("unit_cost[]")

    received_items = []
    for iid, qty_raw, cost_raw in zip(item_ids, qtys, costs):
        if not iid or not qty_raw:
            continue
        try:
            qty = float(qty_raw)
        except ValueError:
            continue
        if qty <= 0:
            continue
        received_items.append({
            "po_item_id": iid, "qty_received": qty,
            "unit_cost": float(cost_raw) if cost_raw else None,
        })

    if not received_items:
        return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?err=Enter+received+quantity+for+at+least+one+item", status_code=303)

    handle_po_receive(db, po, received_items, user.id, user.tenant_id)
    return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?msg=Receipt+recorded", status_code=303)


@router.post("/inventory-v2/purchase-orders/{po_id}/cancel")
def po_cancel(po_id: str, user: User = Depends(_require_inventory_manager), db: Session = Depends(get_db)):
    po = _get_po_or_404(db, po_id, user.tenant_id)
    if po.status in ("RECEIVED", "CANCELLED"):
        return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?err=PO+cannot+be+cancelled+in+current+status", status_code=303)

    was_active = po.status in ("SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED")
    po.status = "CANCELLED"
    po.updated_at = datetime.utcnow()
    db.commit()
    if was_active:
        _apply_in_transit_delta(db, po, -1)
    return RedirectResponse(f"/inventory-v2/purchase-orders/{po_id}?msg=PO+cancelled", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# STOCK RESERVATION ENGINE — Brief 05
# ══════════════════════════════════════════════════════════════════════════════

def reserve_stock_for_item(db, variant_id: str, order_id: str, order_item_id: str,
                            qty: float, agent_id: str, tenant_id: str) -> dict:
    """
    Atomically reserve qty units of a variant for one order line item.
    MUST be called inside an active transaction.
    Uses SELECT FOR UPDATE (row-level lock on PostgreSQL).
    On SQLite (local dev) with_for_update() is silently ignored — test concurrency on Postgres.

    Returns:
      {"success": True,  "available_qty": float}
      {"success": False, "available_qty": float, "in_transit_date": date | None}
    """
    stock = (
        db.query(ProductStock)
        .filter(ProductStock.variant_id == variant_id,
                ProductStock.tenant_id  == tenant_id)
        .with_for_update()
        .first()
    )
    if not stock:
        return {"success": False, "available_qty": 0, "in_transit_date": None}

    if stock.qty_available >= qty:
        stock.qty_available   -= qty
        stock.qty_reserved    += qty
        stock.last_updated_at  = datetime.utcnow()

        db.add(StockReservation(
            tenant_id      = tenant_id,
            variant_id     = variant_id,
            order_id       = order_id,
            order_item_id  = order_item_id,
            qty_reserved   = qty,
            status         = "ACTIVE",
            reserved_by_id = agent_id,
            expires_at     = datetime.utcnow() + timedelta(hours=24),
        ))

        db.add(StockLedgerEntry(
            tenant_id      = tenant_id,
            variant_id     = variant_id,
            movement_type  = "RESERVATION",
            qty            = qty,
            reference_type = "ORDER",
            reference_id   = order_id,
            actor_id       = agent_id,
        ))
        return {"success": True, "available_qty": stock.qty_available}

    else:
        in_transit = (
            db.query(
                func.sum(
                    InventoryPOItem.qty_ordered - InventoryPOItem.qty_received
                ).label("qty"),
                func.min(
                    InventoryPurchaseOrder.expected_arrival_date
                ).label("arrival_date"),
            )
            .join(InventoryPurchaseOrder, InventoryPOItem.po_id == InventoryPurchaseOrder.id)
            .filter(
                InventoryPOItem.variant_id == variant_id,
                InventoryPurchaseOrder.tenant_id == tenant_id,
                InventoryPurchaseOrder.status.in_(
                    ["SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]
                ),
            )
            .first()
        )
        return {
            "success": False,
            "available_qty": stock.qty_available,
            "in_transit_date": (
                in_transit.arrival_date
                if in_transit and in_transit.qty and in_transit.qty > 0
                else None
            ),
        }


def release_all_reservations(db, order_id: str, tenant_id: str, reason: str = ""):
    """Release all ACTIVE reservations for a cancelled/expired order."""
    reservations = (
        db.query(StockReservation)
        .filter(StockReservation.order_id  == order_id,
                StockReservation.tenant_id == tenant_id,
                StockReservation.status    == "ACTIVE")
        .with_for_update()
        .all()
    )
    for res in reservations:
        stock = (
            db.query(ProductStock)
            .filter(ProductStock.variant_id == res.variant_id)
            .with_for_update()
            .first()
        )
        if stock:
            stock.qty_available += res.qty_reserved
            stock.qty_reserved  -= res.qty_reserved
            stock.last_updated_at = datetime.utcnow()

        res.status         = "RELEASED"
        res.released_at    = datetime.utcnow()
        res.release_reason = reason

        db.add(StockLedgerEntry(
            tenant_id      = tenant_id,
            variant_id     = res.variant_id,
            movement_type  = "RELEASE",
            qty            = res.qty_reserved,
            reference_type = "ORDER",
            reference_id   = order_id,
        ))


def fulfill_reservation(db, order_id: str, variant_id: str,
                         qty_dispatched: float, tenant_id: str, actor_id: str):
    """Mark reservation as FULFILLED when the order is dispatched."""
    reservation = (
        db.query(StockReservation)
        .filter(StockReservation.order_id   == order_id,
                StockReservation.variant_id == variant_id,
                StockReservation.tenant_id  == tenant_id,
                StockReservation.status     == "ACTIVE")
        .with_for_update()
        .first()
    )
    if not reservation:
        return

    stock = (
        db.query(ProductStock)
        .filter(ProductStock.variant_id == variant_id)
        .with_for_update()
        .first()
    )
    if stock:
        stock.qty_reserved    -= reservation.qty_reserved
        stock.last_updated_at  = datetime.utcnow()
        # qty_available was already reduced at reservation time; do NOT reduce again.

    reservation.status       = "FULFILLED"
    reservation.fulfilled_at = datetime.utcnow()

    db.add(StockLedgerEntry(
        tenant_id      = tenant_id,
        variant_id     = variant_id,
        movement_type  = "STOCK_OUT",
        qty            = qty_dispatched,
        reference_type = "ORDER",
        reference_id   = order_id,
        actor_id       = actor_id,
    ))


# ══════════════════════════════════════════════════════════════════════════════
# GODOWN DASHBOARD — Upcoming Dispatches / Demand Projection (Brief 05)
# ══════════════════════════════════════════════════════════════════════════════

def get_upcoming_dispatches(db, tenant_id: str):
    from .database import SalesOrder
    return (
        db.query(SalesOrder)
        .filter(SalesOrder.tenant_id  == tenant_id,
                SalesOrder.status     == "CONFIRMED",
                SalesOrder.is_deleted == False)
        .order_by(SalesOrder.expected_delivery_date.asc().nullslast())
        .all()
    )


def get_demand_projection(db, tenant_id: str, days: int = 7):
    from .database import SalesOrder, SalesOrderItem
    cutoff = date.today() + timedelta(days=days)

    demand_rows = (
        db.query(
            SalesOrderItem.variant_id,
            func.sum(
                SalesOrderItem.qty_ordered - SalesOrderItem.qty_dispatched
            ).label("qty_needed"),
        )
        .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
        .filter(
            SalesOrder.tenant_id  == tenant_id,
            SalesOrder.status     == "CONFIRMED",
            SalesOrder.expected_delivery_date != None,
            SalesOrder.expected_delivery_date <= cutoff,
        )
        .group_by(SalesOrderItem.variant_id)
        .all()
    )

    result = []
    for row in demand_rows:
        stock   = db.query(ProductStock).filter(
            ProductStock.variant_id == row.variant_id).first()
        variant = db.query(ProductVariant).filter(ProductVariant.id == row.variant_id).first()
        if not variant:
            continue
        gap = (stock.qty_available if stock else 0) - row.qty_needed

        result.append({
            "product":        variant,
            "qty_needed":     row.qty_needed,
            "qty_available":  stock.qty_available if stock else 0,
            "qty_in_transit": stock.qty_in_transit if stock else 0,
            "gap":            gap,
            "shortfall":      gap < 0,
        })

    return sorted(result, key=lambda r: r["gap"])  # worst shortfall first


@router.get("/inventory-v2/dispatch-queue", response_class=HTMLResponse)
def dispatch_queue(request: Request, user: User = Depends(_require_inventory), db: Session = Depends(get_db)):
    """Read-only godown view of CONFIRMED orders pending dispatch."""
    orders = get_upcoming_dispatches(db, user.tenant_id)
    return templates.TemplateResponse(request, "inventory_v2/dispatch_queue.html", _ctx(
        db, user, orders=orders,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))
