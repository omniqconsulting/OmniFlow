"""
Sales Catalog — Brief 02: Products & Catalog.
Product master, custom attribute schema, media, bulk import/export.
"""
import csv
import io
import json
import uuid as _uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import List, Optional

from .database import get_db, new_id, Product, ProductSchemaField, UnitOfMeasure, User, ProductStock
from .auth import get_current_user, require_admin, require_manager, has_module
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread

router = APIRouter()

PAGE_SIZE = 30
TIER_CHOICES = ("A", "B", "C", "D", "UNRANKED")


def _require_sales(user: User = Depends(get_current_user)) -> User:
    if not has_module(user, "SALES"):
        raise HTTPException(status_code=403, detail="Sales module not enabled for this user")
    return user


def _require_sales_editor(user: User = Depends(get_current_user)) -> User:
    if not has_module(user, "SALES"):
        raise HTTPException(status_code=403, detail="Sales module not enabled for this user")
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


def get_product_or_404(db: Session, product_id: str, tenant_id: str) -> Product:
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.tenant_id == tenant_id,
        Product.is_deleted == False,
    ).first()
    if not product:
        raise HTTPException(404, "Product not found")
    return product


def _active_schema_fields(db: Session, tenant_id: str):
    return (
        db.query(ProductSchemaField)
        .filter(ProductSchemaField.tenant_id == tenant_id, ProductSchemaField.is_active == True)
        .order_by(ProductSchemaField.sort_order)
        .all()
    )


def _active_units(db: Session, tenant_id: str):
    return (
        db.query(UnitOfMeasure)
        .filter(UnitOfMeasure.tenant_id == tenant_id, UnitOfMeasure.is_active == True, UnitOfMeasure.is_deleted == False)
        .order_by(UnitOfMeasure.name)
        .all()
    )


def _existing_categories(db: Session, tenant_id: str):
    rows = (
        db.query(Product.category)
        .filter(Product.tenant_id == tenant_id, Product.is_deleted == False, Product.category.isnot(None))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


def _parse_attributes_from_form(form, schema_fields) -> dict:
    attrs = {}
    for field in schema_fields:
        if field.field_type == "boolean":
            attrs[field.label] = "true" if form.get(f"attr__{field.label}") else "false"
        else:
            val = form.get(f"attr__{field.label}", "")
            if val:
                attrs[field.label] = val
    return attrs


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT LIST
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog", response_class=HTMLResponse)
def catalog_list(
    request: Request,
    q: str = "",
    category: str = "",
    tier: str = "",
    active: str = "",
    page: int = 1,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    query = db.query(Product).filter(Product.tenant_id == user.tenant_id, Product.is_deleted == False)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Product.name.ilike(like), Product.sku_code.ilike(like)))
    if category:
        query = query.filter(Product.category == category)
    if tier:
        query = query.filter(Product.product_tier == tier)
    if active in ("true", "false"):
        query = query.filter(Product.is_active == (active == "true"))

    query = query.order_by(Product.name)
    total = query.count()
    products = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    return templates.TemplateResponse(request, "sales/catalog_list.html", _ctx(
        db, user,
        products=products, total=total, page=page, page_size=PAGE_SIZE,
        q=q, category=category, tier=tier, active=active,
        categories=_existing_categories(db, user.tenant_id),
        tier_choices=TIER_CHOICES,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


# ══════════════════════════════════════════════════════════════════════════════
# CREATE / EDIT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog/new", response_class=HTMLResponse)
def catalog_new_form(request: Request, user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "sales/catalog_new.html", _ctx(
        db, user,
        product=None, attributes={},
        schema_fields=_active_schema_fields(db, user.tenant_id),
        units=_active_units(db, user.tenant_id),
        categories=_existing_categories(db, user.tenant_id),
        err="",
    ))


@router.post("/sales/catalog/create")
async def catalog_create(
    request: Request,
    sku_code: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    base_unit_id: str = Form(""),
    low_stock_threshold: str = Form(""),
    is_active: Optional[str] = Form(None),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    sku_code = sku_code.strip()
    name = name.strip()
    schema_fields = _active_schema_fields(db, user.tenant_id)

    existing = db.query(Product).filter(
        Product.tenant_id == user.tenant_id,
        Product.sku_code == sku_code,
        Product.is_deleted == False,
    ).first()
    if existing:
        form = await request.form()
        return templates.TemplateResponse(request, "sales/catalog_new.html", _ctx(
            db, user,
            product={"sku_code": sku_code, "name": name, "description": description,
                     "category": category, "base_unit_id": base_unit_id,
                     "low_stock_threshold": low_stock_threshold, "is_active": bool(is_active)},
            attributes=_parse_attributes_from_form(form, schema_fields),
            schema_fields=schema_fields,
            units=_active_units(db, user.tenant_id),
            categories=_existing_categories(db, user.tenant_id),
            err="SKU code already exists",
        ), status_code=400)

    form = await request.form()
    attributes = _parse_attributes_from_form(form, schema_fields)

    product = Product(
        id=new_id(),
        tenant_id=user.tenant_id,
        sku_code=sku_code,
        name=name,
        description=description.strip() or None,
        category=category.strip() or None,
        base_unit_id=base_unit_id or None,
        attributes_json=json.dumps(attributes),
        low_stock_threshold=float(low_stock_threshold) if low_stock_threshold else None,
        is_active=bool(is_active),
        created_by_id=user.id,
    )
    db.add(product)
    db.commit()
    db.add(ProductStock(
        product_id=product.id,
        tenant_id=user.tenant_id,
        qty_available=0.0,
        qty_reserved=0.0,
        qty_in_transit=0.0,
    ))
    db.commit()
    return RedirectResponse(f"/sales/catalog/{product.id}?msg=Product+created", status_code=303)


@router.get("/sales/catalog/{product_id}/edit", response_class=HTMLResponse)
def catalog_edit_form(product_id: str, request: Request, user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    product = get_product_or_404(db, product_id, user.tenant_id)
    return templates.TemplateResponse(request, "sales/catalog_edit.html", _ctx(
        db, user,
        product=product, attributes=json.loads(product.attributes_json or "{}"),
        schema_fields=_active_schema_fields(db, user.tenant_id),
        units=_active_units(db, user.tenant_id),
        categories=_existing_categories(db, user.tenant_id),
        err="",
    ))


@router.post("/sales/catalog/{product_id}/edit")
async def catalog_edit_save(
    product_id: str,
    request: Request,
    sku_code: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    base_unit_id: str = Form(""),
    low_stock_threshold: str = Form(""),
    is_active: Optional[str] = Form(None),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id, user.tenant_id)
    sku_code = sku_code.strip()
    schema_fields = _active_schema_fields(db, user.tenant_id)

    existing = db.query(Product).filter(
        Product.tenant_id == user.tenant_id,
        Product.sku_code == sku_code,
        Product.is_deleted == False,
        Product.id != product_id,
    ).first()
    if existing:
        form = await request.form()
        return templates.TemplateResponse(request, "sales/catalog_edit.html", _ctx(
            db, user,
            product=product,
            attributes=_parse_attributes_from_form(form, schema_fields),
            schema_fields=schema_fields,
            units=_active_units(db, user.tenant_id),
            categories=_existing_categories(db, user.tenant_id),
            err="SKU code already exists",
        ), status_code=400)

    form = await request.form()
    product.sku_code = sku_code
    product.name = name.strip()
    product.description = description.strip() or None
    product.category = category.strip() or None
    product.base_unit_id = base_unit_id or None
    product.low_stock_threshold = float(low_stock_threshold) if low_stock_threshold else None
    product.is_active = bool(is_active)
    product.attributes_json = json.dumps(_parse_attributes_from_form(form, schema_fields))
    product.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/sales/catalog/{product.id}?msg=Product+updated", status_code=303)


@router.post("/sales/catalog/{product_id}/delete")
def catalog_delete(product_id: str, user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    product = get_product_or_404(db, product_id, user.tenant_id)
    product.is_deleted = True
    db.commit()
    return RedirectResponse("/sales/catalog?msg=Product+deleted", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sales/catalog/{product_id}/upload-media")
async def upload_product_media(
    product_id: str,
    files: List[UploadFile] = File(...),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id, user.tenant_id)
    existing = json.loads(product.media_urls_json or "[]")

    if len(existing) + len(files) > 8:
        raise HTTPException(400, f"Max 8 photos allowed. Currently {len(existing)}.")

    for file in files:
        if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
            raise HTTPException(400, f"Unsupported file type: {file.content_type}")

        content = await file.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(400, f"{file.filename} exceeds 5MB limit.")

        ext = file.filename.rsplit(".", 1)[-1].lower()
        filename = f"{_uuid.uuid4().hex}.{ext}"
        rel_path = f"uploads/{user.tenant_id}/products/{product_id}/{filename}"
        full_path = Path(__file__).parent / "static" / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        existing.append(rel_path)

    product.media_urls_json = json.dumps(existing)
    product.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/sales/catalog/{product_id}?msg=Photos+uploaded", status_code=303)


@router.post("/sales/catalog/{product_id}/delete-media")
def delete_product_media(
    product_id: str,
    index: int = Form(...),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id, user.tenant_id)
    existing = json.loads(product.media_urls_json or "[]")
    if 0 <= index < len(existing):
        existing.pop(index)
        product.media_urls_json = json.dumps(existing)
        product.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(f"/sales/catalog/{product_id}?msg=Photo+removed", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA MANAGEMENT (Admin only)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog/schema", response_class=HTMLResponse)
def catalog_schema_page(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    fields = (
        db.query(ProductSchemaField)
        .filter(ProductSchemaField.tenant_id == user.tenant_id)
        .order_by(ProductSchemaField.sort_order)
        .all()
    )
    return templates.TemplateResponse(request, "sales/catalog_schema.html", _ctx(
        db, user, fields=fields,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/sales/catalog/schema/add-field")
def schema_add_field(
    label: str = Form(...),
    field_type: str = Form("text"),
    is_required: Optional[str] = Form(None),
    options: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    label = label.strip()
    if not label:
        return RedirectResponse("/sales/catalog/schema?err=Label+is+required", status_code=303)
    max_order = db.query(ProductSchemaField).filter(ProductSchemaField.tenant_id == user.tenant_id).count()
    options_list = [o.strip() for o in options.split(",") if o.strip()] if field_type == "dropdown" else []
    db.add(ProductSchemaField(
        id=new_id(), tenant_id=user.tenant_id, label=label, field_type=field_type,
        options_json=json.dumps(options_list), sort_order=max_order,
        is_required=bool(is_required),
    ))
    db.commit()
    return RedirectResponse("/sales/catalog/schema?msg=Field+added", status_code=303)


@router.post("/sales/catalog/schema/field/{field_id}/edit")
def schema_edit_field(
    field_id: str,
    label: str = Form(...),
    field_type: str = Form("text"),
    is_required: Optional[str] = Form(None),
    options: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    field = db.query(ProductSchemaField).filter(
        ProductSchemaField.id == field_id, ProductSchemaField.tenant_id == user.tenant_id,
    ).first()
    if not field:
        raise HTTPException(404, "Field not found")
    field.label = label.strip()
    field.field_type = field_type
    field.is_required = bool(is_required)
    field.options_json = json.dumps([o.strip() for o in options.split(",") if o.strip()]) if field_type == "dropdown" else "[]"
    db.commit()
    return RedirectResponse("/sales/catalog/schema?msg=Field+updated", status_code=303)


@router.post("/sales/catalog/schema/field/{field_id}/delete")
def schema_delete_field(field_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    field = db.query(ProductSchemaField).filter(
        ProductSchemaField.id == field_id, ProductSchemaField.tenant_id == user.tenant_id,
    ).first()
    if not field:
        raise HTTPException(404, "Field not found")
    field.is_active = False
    db.commit()
    return RedirectResponse("/sales/catalog/schema?msg=Field+deactivated", status_code=303)


@router.post("/sales/catalog/schema/reorder")
async def schema_reorder(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    body = await request.json()
    for field_id, new_order in body.items():
        field = db.query(ProductSchemaField).filter(
            ProductSchemaField.id == field_id, ProductSchemaField.tenant_id == user.tenant_id,
        ).first()
        if field:
            field.sort_order = int(new_order)
    db.commit()
    return JSONResponse({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
# BULK OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog/bulk-upload", response_class=HTMLResponse)
def bulk_upload_page(request: Request, user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "sales/catalog_bulk_upload.html", _ctx(db, user))


@router.get("/sales/catalog/bulk-template")
def bulk_template(user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    schema_fields = _active_schema_fields(db, user.tenant_id)
    cols = ["sku_code", "name", "description", "category", "unit_abbreviation", "low_stock_threshold"]
    cols += [f.label for f in schema_fields]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=product_template.csv"},
    )


def _validate_product_row(row, tenant_id, db, schema_fields, existing_skus):
    errors = []
    sku = (row.get("sku_code") or "").strip()
    if not sku:
        errors.append("SKU code is required")
    elif sku in existing_skus:
        errors.append(f"SKU '{sku}' already exists")
    if not (row.get("name") or "").strip():
        errors.append("Name is required")
    unit_abbr = (row.get("unit_abbreviation") or "").strip()
    if unit_abbr:
        unit = db.query(UnitOfMeasure).filter(
            UnitOfMeasure.tenant_id == tenant_id,
            UnitOfMeasure.abbreviation == unit_abbr,
            UnitOfMeasure.is_active == True,
        ).first()
        if not unit:
            errors.append(f"Unit '{unit_abbr}' not found. Add it in Setup → Units first.")
    return errors


@router.post("/sales/catalog/bulk-upload")
async def bulk_upload(
    file: UploadFile = File(...),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    schema_fields = _active_schema_fields(db, user.tenant_id)
    existing_skus = {
        r[0] for r in db.query(Product.sku_code).filter(
            Product.tenant_id == user.tenant_id, Product.is_deleted == False,
        ).all()
    }

    results = []
    valid_count = 0
    seen_in_file = set()
    for i, row in enumerate(rows, start=2):
        errors = _validate_product_row(row, user.tenant_id, db, schema_fields, existing_skus | seen_in_file)
        sku = (row.get("sku_code") or "").strip()
        if not errors:
            valid_count += 1
            seen_in_file.add(sku)
        else:
            results.append({"row": i, "sku": sku, "errors": errors})

    return JSONResponse({
        "total": len(rows),
        "valid": valid_count,
        "errors": results,
        "rows": rows,
    })


@router.post("/sales/catalog/bulk-upload/confirm")
async def bulk_upload_confirm(request: Request, user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    body = await request.json()
    rows = body.get("rows", [])
    schema_fields = _active_schema_fields(db, user.tenant_id)
    schema_labels = {f.label for f in schema_fields}
    existing_skus = {
        r[0] for r in db.query(Product.sku_code).filter(
            Product.tenant_id == user.tenant_id, Product.is_deleted == False,
        ).all()
    }

    created = 0
    skipped = 0
    for row in rows:
        errors = _validate_product_row(row, user.tenant_id, db, schema_fields, existing_skus)
        if errors:
            skipped += 1
            continue
        unit_abbr = (row.get("unit_abbreviation") or "").strip()
        unit = None
        if unit_abbr:
            unit = db.query(UnitOfMeasure).filter(
                UnitOfMeasure.tenant_id == user.tenant_id,
                UnitOfMeasure.abbreviation == unit_abbr,
                UnitOfMeasure.is_active == True,
            ).first()
        attrs = {k: v for k, v in row.items() if k in schema_labels and v}
        product = Product(
            id=new_id(), tenant_id=user.tenant_id,
            sku_code=row.get("sku_code", "").strip(),
            name=row.get("name", "").strip(),
            description=(row.get("description") or "").strip() or None,
            category=(row.get("category") or "").strip() or None,
            base_unit_id=unit.id if unit else None,
            low_stock_threshold=float(row["low_stock_threshold"]) if (row.get("low_stock_threshold") or "").strip() else None,
            attributes_json=json.dumps(attrs),
            created_by_id=user.id,
        )
        db.add(product)
        existing_skus.add(product.sku_code)
        created += 1
        db.flush()
        db.add(ProductStock(
            product_id=product.id,
            tenant_id=user.tenant_id,
            qty_available=0.0,
            qty_reserved=0.0,
            qty_in_transit=0.0,
        ))

    db.commit()
    return JSONResponse({"created": created, "skipped": skipped})


@router.get("/sales/catalog/export")
def catalog_export(user: User = Depends(_require_sales), db: Session = Depends(get_db)):
    schema_fields = _active_schema_fields(db, user.tenant_id)
    products = db.query(Product).filter(
        Product.tenant_id == user.tenant_id, Product.is_deleted == False,
    ).order_by(Product.name).all()

    cols = ["sku_code", "name", "description", "category", "unit", "tier", "is_active", "low_stock_threshold"]
    cols += [f.label for f in schema_fields]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    for p in products:
        attrs = json.loads(p.attributes_json or "{}")
        row = [
            p.sku_code, p.name, p.description or "", p.category or "",
            p.base_unit.abbreviation if p.base_unit else "",
            p.product_tier, p.is_active, p.low_stock_threshold or "",
        ]
        row += [attrs.get(f.label, "") for f in schema_fields]
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=products_export.csv"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# DETAIL — registered last: catch-all {product_id} must not shadow literal
# paths above (schema, bulk-upload, bulk-template, export).
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog/{product_id}", response_class=HTMLResponse)
def catalog_detail(product_id: str, request: Request, user: User = Depends(_require_sales), db: Session = Depends(get_db)):
    from sqlalchemy import func
    from .database import InventoryPurchaseOrder, InventoryPOItem

    product = get_product_or_404(db, product_id, user.tenant_id)

    stock = db.query(ProductStock).filter(
        ProductStock.product_id == product_id,
        ProductStock.tenant_id == user.tenant_id,
    ).first()

    in_transit_info = (
        db.query(
            func.sum(InventoryPOItem.qty_ordered - InventoryPOItem.qty_received).label("qty"),
            func.min(InventoryPurchaseOrder.expected_arrival_date).label("arrival_date"),
        )
        .join(InventoryPurchaseOrder, InventoryPOItem.po_id == InventoryPurchaseOrder.id)
        .filter(
            InventoryPOItem.product_id == product_id,
            InventoryPurchaseOrder.tenant_id == user.tenant_id,
            InventoryPurchaseOrder.status.in_(["SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]),
        )
        .first()
    )

    return templates.TemplateResponse(request, "sales/catalog_detail.html", _ctx(
        db, user,
        product=product,
        attributes=json.loads(product.attributes_json or "{}"),
        media_urls=json.loads(product.media_urls_json or "[]"),
        stock=stock,
        in_transit_qty=in_transit_info.qty if in_transit_info else None,
        in_transit_arrival=in_transit_info.arrival_date if in_transit_info else None,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))
