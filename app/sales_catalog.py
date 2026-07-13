"""
Sales Catalog — Catalog Hierarchy Phase 1.
Category -> SubCategory -> Product (parent, shared attrs) -> Variant (the
sellable SKU). Product master, custom attribute schema, media, bulk import/export.
"""
import csv
import io
import json
import uuid as _uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import List, Optional

from .database import (
    get_db, new_id, Product, ProductVariant, ProductSchemaField, UnitOfMeasure,
    User, ProductStock, Category, SubCategory, EndProduct,
    InventoryPurchaseOrder, InventoryPOItem, PurchaseRequest,
)
from .auth import (
    get_current_user, require_admin, require_admin_or_redirect,
    require_manager, has_module, require_module,
)
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread, generate_product_sku
from .constants import BULK_IMPORT_MAX_ROWS
from .bulk_common import check_required_headers
from .sales_catalog_sync import (
    sync_end_product_from_variant, remove_end_product_for_variant,
    resolve_or_create_hierarchy, attach_drive_photo,
)

router = APIRouter()

PAGE_SIZE = 30
TIER_CHOICES = ("A", "B", "C", "D", "UNRANKED")

_require_sales = require_module("SALES", "SALES_MODULE")
_require_sales_or_redirect = require_module("SALES", "SALES_MODULE", redirect_unauthenticated=True)


def _require_sales_editor(user: User = Depends(_require_sales)) -> User:
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    return user

def _require_sales_editor_or_redirect(user: User = Depends(_require_sales_or_redirect)) -> User:
    """Same check as _require_sales_editor, for GET page routes: missing/invalid
    session redirects to /login instead of raw 401 JSON."""
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    return user


def _require_sales_admin(user: User = Depends(_require_sales)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin only")
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


def get_variant_or_404(db: Session, variant_id: str, tenant_id: str) -> ProductVariant:
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id,
        ProductVariant.tenant_id == tenant_id,
        ProductVariant.is_deleted == False,
    ).first()
    if not variant:
        raise HTTPException(404, "Variant not found")
    return variant


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


def _active_categories(db: Session, tenant_id: str):
    return (
        db.query(Category)
        .filter(Category.tenant_id == tenant_id, Category.is_active == True, Category.is_deleted == False)
        .order_by(Category.name)
        .all()
    )


def _active_subcategories(db: Session, tenant_id: str, category_id: str = None):
    q = db.query(SubCategory).filter(
        SubCategory.tenant_id == tenant_id, SubCategory.is_active == True, SubCategory.is_deleted == False,
    )
    if category_id:
        q = q.filter(SubCategory.category_id == category_id)
    return q.order_by(SubCategory.name).all()


def _parse_attributes_from_form(form, schema_fields, prefix="attr__") -> dict:
    attrs = {}
    for field in schema_fields:
        if field.field_type == "boolean":
            attrs[field.label] = "true" if form.get(f"{prefix}{field.label}") else "false"
        else:
            val = form.get(f"{prefix}{field.label}", "")
            if val:
                attrs[field.label] = val
    return attrs


def _build_variant_label(sku_code: str, explicit_label: str, attrs: dict) -> str:
    if explicit_label and explicit_label.strip():
        return explicit_label.strip()
    if attrs:
        return " / ".join(str(v) for v in attrs.values())
    return sku_code


# ══════════════════════════════════════════════════════════════════════════════
# CATEGORY / SUB-CATEGORY
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog/categories", response_class=HTMLResponse)
def categories_page(request: Request, user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
    categories = db.query(Category).filter(
        Category.tenant_id == user.tenant_id, Category.is_deleted == False,
    ).order_by(Category.name).all()
    subcats_by_cat = {}
    for sc in db.query(SubCategory).filter(SubCategory.tenant_id == user.tenant_id, SubCategory.is_deleted == False).order_by(SubCategory.name).all():
        subcats_by_cat.setdefault(sc.category_id, []).append(sc)
    return templates.TemplateResponse(request, "sales/catalog_categories.html", _ctx(
        db, user, categories=categories, subcats_by_cat=subcats_by_cat,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/sales/catalog/categories/add")
def category_add(name: str = Form(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        return RedirectResponse("/sales/catalog/categories?err=Name+is+required", status_code=303)
    db.add(Category(id=new_id(), tenant_id=user.tenant_id, name=name))
    db.commit()
    return RedirectResponse("/sales/catalog/categories?msg=Category+added", status_code=303)


@router.post("/sales/catalog/categories/{category_id}/delete")
def category_delete(category_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.id == category_id, Category.tenant_id == user.tenant_id).first()
    if cat:
        cat.is_deleted = True
        db.commit()
    return RedirectResponse("/sales/catalog/categories?msg=Category+removed", status_code=303)


@router.post("/sales/catalog/subcategories/add")
def subcategory_add(
    category_id: str = Form(...), name: str = Form(...),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        return RedirectResponse("/sales/catalog/categories?err=Name+is+required", status_code=303)
    db.add(SubCategory(id=new_id(), tenant_id=user.tenant_id, category_id=category_id, name=name))
    db.commit()
    return RedirectResponse("/sales/catalog/categories?msg=Sub-category+added", status_code=303)


@router.post("/sales/catalog/subcategories/{subcategory_id}/delete")
def subcategory_delete(subcategory_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    sc = db.query(SubCategory).filter(SubCategory.id == subcategory_id, SubCategory.tenant_id == user.tenant_id).first()
    if sc:
        sc.is_deleted = True
        db.commit()
    return RedirectResponse("/sales/catalog/categories?msg=Sub-category+removed", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT LIST (Category -> Sub-Category tree, Product/Variant list on the right)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog", response_class=HTMLResponse)
def catalog_list(
    request: Request,
    q: str = "",
    category_id: list = Query(default=[]),
    sub_category_id: list = Query(default=[]),
    tier: str = "",
    active: str = "",
    view: str = "grid",
    page: int = 1,
    user: User = Depends(_require_sales_or_redirect),
    db: Session = Depends(get_db),
):
    query = db.query(Product).filter(Product.tenant_id == user.tenant_id, Product.is_deleted == False)
    if q:
        like = f"%{q}%"
        query = query.join(ProductVariant, ProductVariant.product_id == Product.id, isouter=True).filter(
            or_(Product.name.ilike(like), ProductVariant.sku_code.ilike(like))
        ).distinct()
    if sub_category_id:
        query = query.filter(Product.sub_category_id.in_(sub_category_id))
    elif category_id:
        sub_ids = []
        for cid in category_id:
            sub_ids += [sc.id for sc in _active_subcategories(db, user.tenant_id, cid)]
        query = query.filter(Product.sub_category_id.in_(sub_ids))
    if tier:
        query = query.join(ProductVariant, ProductVariant.product_id == Product.id).filter(
            ProductVariant.product_tier == tier, ProductVariant.is_deleted == False,
        ).distinct()
    if active in ("true", "false"):
        query = query.filter(Product.is_active == (active == "true"))

    query = query.order_by(Product.name)
    total = query.count()
    products = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    categories = _active_categories(db, user.tenant_id)
    subcategories = _active_subcategories(db, user.tenant_id)

    # ── Per-product stock status: In Stock / Expected date / Requested / none ──
    live_variants_by_product = {p.id: [v for v in p.variants if not v.is_deleted] for p in products}
    all_variant_ids = [v.id for vids in live_variants_by_product.values() for v in vids]
    stock_by_variant = {
        s.variant_id: s for s in db.query(ProductStock).filter(
            ProductStock.tenant_id == user.tenant_id,
            ProductStock.variant_id.in_(all_variant_ids),
            ProductStock.branch_id.is_(None),
        ).all()
    } if all_variant_ids else {}

    open_po_rows = db.query(InventoryPOItem.variant_id, InventoryPurchaseOrder.expected_arrival_date).join(
        InventoryPurchaseOrder, InventoryPOItem.po_id == InventoryPurchaseOrder.id,
    ).filter(
        InventoryPurchaseOrder.tenant_id == user.tenant_id,
        InventoryPurchaseOrder.status.in_(["SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]),
        InventoryPOItem.variant_id.in_(all_variant_ids),
    ).all() if all_variant_ids else []
    expected_date_by_variant: dict = {}
    for variant_id, expected_date in open_po_rows:
        if variant_id not in expected_date_by_variant or (
            expected_date and (not expected_date_by_variant[variant_id] or expected_date < expected_date_by_variant[variant_id])
        ):
            expected_date_by_variant[variant_id] = expected_date

    requested_variant_ids = {
        r[0] for r in db.query(PurchaseRequest.variant_id).filter(
            PurchaseRequest.tenant_id == user.tenant_id, PurchaseRequest.status == "PENDING",
            PurchaseRequest.variant_id.in_(all_variant_ids),
        ).all()
    } if all_variant_ids else set()

    stock_status_by_product: dict = {}
    for p in products:
        variant_ids = [v.id for v in live_variants_by_product[p.id]]
        total_available = sum(
            (stock_by_variant[vid].qty_available or 0) for vid in variant_ids if vid in stock_by_variant
        )
        total_in_transit = sum(
            (stock_by_variant[vid].qty_in_transit or 0) for vid in variant_ids if vid in stock_by_variant
        )
        if total_available > 0:
            stock_status_by_product[p.id] = {"state": "IN_STOCK", "qty": total_available, "in_transit": total_in_transit}
            continue
        expected_dates = [expected_date_by_variant[vid] for vid in variant_ids if vid in expected_date_by_variant]
        if expected_dates:
            stock_status_by_product[p.id] = {
                "state": "EXPECTED", "date": min((d for d in expected_dates if d), default=None),
                "in_transit": total_in_transit,
            }
            continue
        if any(vid in requested_variant_ids for vid in variant_ids):
            stock_status_by_product[p.id] = {"state": "REQUESTED", "in_transit": total_in_transit}
            continue
        stock_status_by_product[p.id] = {
            "state": "NONE", "variant_id": variant_ids[0] if variant_ids else None, "in_transit": total_in_transit,
        }

    # Redesign (2026-07): List view's Tier column shows a single tier per
    # product even though tier actually lives on ProductVariant — "Mixed"
    # when a product's live variants disagree.
    tier_display_by_product: dict = {}
    for p in products:
        tiers = {v.product_tier for v in live_variants_by_product[p.id]}
        tier_display_by_product[p.id] = tiers.pop() if len(tiers) == 1 else ("Mixed" if tiers else "UNRANKED")

    cat_template_name = "sales/catalog_list_mobile.html" if request.cookies.get("pwa_ui") == "1" else "sales/catalog_list.html"
    return templates.TemplateResponse(request, cat_template_name, _ctx(
        db, user,
        products=products, total=total, page=page, page_size=PAGE_SIZE, view=view if view in ("grid", "list") else "grid",
        q=q, category_id=category_id, sub_category_id=sub_category_id, tier=tier, active=active,
        tier_display_by_product=tier_display_by_product,
        categories=categories, subcategories=subcategories,
        tier_choices=TIER_CHOICES,
        stock_status_by_product=stock_status_by_product,
        # New-Product modal (Phase 4 of the UX redesign) needs the same
        # subcategory-by-id shape and reference data as catalog_new.html.
        new_product_subcats_by_id={sc.id: sc for sc in subcategories},
        units=_active_units(db, user.tenant_id),
        schema_fields=_active_schema_fields(db, user.tenant_id),
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


# ══════════════════════════════════════════════════════════════════════════════
# PURCHASE REQUESTS — sales agent flags an out-of-stock, no-open-PO product;
# surfaces on Inventory's Purchase Orders page for approval.
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sales/catalog/{variant_id}/request-po")
def catalog_request_po(
    variant_id: str,
    qty_requested: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id, ProductVariant.tenant_id == user.tenant_id,
        ProductVariant.is_deleted == False,
    ).first()
    if not variant:
        return RedirectResponse("/sales/catalog?err=Variant+not+found", status_code=303)

    existing = db.query(PurchaseRequest).filter(
        PurchaseRequest.tenant_id == user.tenant_id, PurchaseRequest.variant_id == variant_id,
        PurchaseRequest.status == "PENDING",
    ).first()
    if existing:
        return RedirectResponse("/sales/catalog?msg=Purchase+already+requested", status_code=303)

    qty = None
    if qty_requested.strip():
        try:
            qty = float(qty_requested)
        except ValueError:
            qty = None

    db.add(PurchaseRequest(
        tenant_id=user.tenant_id, variant_id=variant_id, requested_by_id=user.id,
        qty_requested=qty, notes=notes.strip() or None,
    ))
    db.commit()
    return RedirectResponse("/sales/catalog?msg=Purchase+request+sent+to+Inventory", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# CREATE / EDIT PRODUCT (+ inline variants on create)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog/new", response_class=HTMLResponse)
def catalog_new_form(request: Request, user: User = Depends(_require_sales_editor_or_redirect), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "sales/catalog_new.html", _ctx(
        db, user,
        product=None, attributes={},
        schema_fields=_active_schema_fields(db, user.tenant_id),
        units=_active_units(db, user.tenant_id),
        categories=_active_categories(db, user.tenant_id),
        subcats_by_cat={sc.id: sc for sc in _active_subcategories(db, user.tenant_id)},
        err="",
    ))


@router.post("/sales/catalog/create")
async def catalog_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    sub_category_id: str = Form(""),
    base_unit_id: str = Form(""),
    source: str = Form(""),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    err_redirect = "/setup/end-products" if source == "setup" else "/sales/catalog"
    name = name.strip()
    schema_fields = _active_schema_fields(db, user.tenant_id)
    form = await request.form()
    attributes = _parse_attributes_from_form(form, schema_fields)

    sub_category = db.query(SubCategory).filter(SubCategory.id == sub_category_id).first() if sub_category_id else None
    category = sub_category.category if sub_category else None

    # Inline variant rows: variant_sku[], variant_label[], variant_unit_id[], variant_low_stock[]
    # A blank SKU is not dropped — it's auto-generated (CC-SSS-#### from the
    # product's category/sub-category), matching Setup's End Products behavior.
    skus = form.getlist("variant_sku[]")
    labels = form.getlist("variant_label[]")
    unit_ids = form.getlist("variant_unit_id[]")
    thresholds = form.getlist("variant_low_stock[]")

    variant_rows = []
    seen_skus = set()
    has_row = False
    for i, sku in enumerate(skus):
        label = labels[i].strip() if i < len(labels) else ""
        sku = (sku or "").strip()
        if not sku and not label and (i >= len(unit_ids) or not unit_ids[i]) and (i >= len(thresholds) or not thresholds[i]):
            continue  # fully empty row
        has_row = True
        if not sku:
            sku = generate_product_sku(
                db, user.tenant_id,
                category.name if category else None,
                sub_category.name if sub_category else None,
            )
        if sku in seen_skus:
            return RedirectResponse(f"{err_redirect}?err=Duplicate+SKU+'{sku}'+in+the+form", status_code=303)
        seen_skus.add(sku)
        existing = db.query(ProductVariant).filter(
            ProductVariant.tenant_id == user.tenant_id, ProductVariant.sku_code == sku,
            ProductVariant.is_deleted == False,
        ).first()
        if existing:
            return RedirectResponse(f"{err_redirect}?err=SKU+'{sku}'+already+exists", status_code=303)
        variant_rows.append({
            "sku": sku,
            "label": label,
            "unit_id": unit_ids[i] if i < len(unit_ids) and unit_ids[i] else None,
            "threshold": thresholds[i] if i < len(thresholds) and thresholds[i] else None,
        })

    if not has_row and source == "mobile":
        # Mobile's "New Product" sheet only collects name + category (full
        # variant/SKU entry stays desktop-only per design) — auto-generate a
        # single blank variant so the product is still sellable immediately.
        has_row = True
        auto_sku = generate_product_sku(
            db, user.tenant_id,
            category.name if category else None,
            sub_category.name if sub_category else None,
        )
        variant_rows.append({"sku": auto_sku, "label": "", "unit_id": None, "threshold": None})
    elif not has_row:
        return RedirectResponse(
            f"{err_redirect}?err=At+least+one+Variant+row+is+required+-+a+Product+alone+isn't+sellable",
            status_code=303,
        )

    product = Product(
        id=new_id(), tenant_id=user.tenant_id, name=name,
        description=description.strip() or None,
        sub_category_id=sub_category_id or None,
        base_unit_id=base_unit_id or None,
        attributes_json=json.dumps(attributes),
        created_by_id=user.id,
    )
    db.add(product)
    db.flush()

    for vr in variant_rows:
        variant = ProductVariant(
            id=new_id(), tenant_id=user.tenant_id, product_id=product.id,
            sku_code=vr["sku"], variant_label=_build_variant_label(vr["sku"], vr["label"], {}),
            base_unit_id=vr["unit_id"],
            low_stock_threshold=float(vr["threshold"]) if vr["threshold"] else None,
            created_by_id=user.id,
        )
        db.add(variant)
        db.flush()
        db.add(ProductStock(variant_id=variant.id, tenant_id=user.tenant_id))
        sync_end_product_from_variant(db, variant)
        db.flush()

    db.commit()
    if source == "setup":
        return RedirectResponse("/setup/end-products?msg=Product+created", status_code=303)
    if source == "mobile":
        return RedirectResponse("/sales/catalog?msg=Product+created", status_code=303)
    return RedirectResponse(f"/sales/catalog/{product.id}?msg=Product+created", status_code=303)


@router.get("/sales/catalog/{product_id}/edit", response_class=HTMLResponse)
def catalog_edit_form(product_id: str, request: Request, user: User = Depends(_require_sales_editor_or_redirect), db: Session = Depends(get_db)):
    product = get_product_or_404(db, product_id, user.tenant_id)
    return templates.TemplateResponse(request, "sales/catalog_edit.html", _ctx(
        db, user,
        product=product, attributes=json.loads(product.attributes_json or "{}"),
        schema_fields=_active_schema_fields(db, user.tenant_id),
        units=_active_units(db, user.tenant_id),
        categories=_active_categories(db, user.tenant_id),
        subcats_by_cat={sc.id: sc for sc in _active_subcategories(db, user.tenant_id)},
        tier_choices=TIER_CHOICES,
        err="",
    ))


@router.post("/sales/catalog/{product_id}/edit")
async def catalog_edit_save(
    product_id: str,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    sub_category_id: str = Form(""),
    base_unit_id: str = Form(""),
    is_active: Optional[str] = Form(None),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id, user.tenant_id)
    schema_fields = _active_schema_fields(db, user.tenant_id)
    form = await request.form()

    product.name = name.strip()
    product.description = description.strip() or None
    product.sub_category_id = sub_category_id or None
    product.base_unit_id = base_unit_id or None
    product.is_active = bool(is_active)
    product.attributes_json = json.dumps(_parse_attributes_from_form(form, schema_fields))
    product.updated_at = datetime.utcnow()
    db.flush()

    # Product-level fields (name, category, active flag) feed the mirrored
    # EndProduct via each variant — keep Catalog and Setup's End Products in sync.
    for variant in db.query(ProductVariant).filter(
        ProductVariant.product_id == product.id, ProductVariant.is_deleted == False,
    ).all():
        sync_end_product_from_variant(db, variant)

    db.commit()
    return RedirectResponse(f"/sales/catalog/{product.id}?msg=Product+updated", status_code=303)


@router.post("/sales/catalog/{product_id}/delete")
def catalog_delete(product_id: str, user: User = Depends(_require_sales_admin), db: Session = Depends(get_db)):
    product = get_product_or_404(db, product_id, user.tenant_id)
    product.is_deleted = True
    for v in db.query(ProductVariant).filter(ProductVariant.product_id == product.id, ProductVariant.is_deleted == False).all():
        v.is_deleted = True
        remove_end_product_for_variant(db, v)
    db.commit()
    return RedirectResponse("/sales/catalog?msg=Product+deleted", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT CRUD (nested under a Product)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sales/catalog/{product_id}/variants/add")
async def variant_add(
    product_id: str,
    request: Request,
    sku_code: str = Form(""),
    variant_label: str = Form(""),
    base_unit_id: str = Form(""),
    low_stock_threshold: str = Form(""),
    product_tier: str = Form("UNRANKED"),
    is_active: Optional[str] = Form(None),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id, user.tenant_id)
    sku_code = sku_code.strip()
    if not sku_code:
        sub_category = product.sub_category
        category = sub_category.category if sub_category else None
        sku_code = generate_product_sku(
            db, user.tenant_id,
            category.name if category else None,
            sub_category.name if sub_category else None,
        )
    existing = db.query(ProductVariant).filter(
        ProductVariant.tenant_id == user.tenant_id, ProductVariant.sku_code == sku_code,
        ProductVariant.is_deleted == False,
    ).first()
    if existing:
        return RedirectResponse(f"/sales/catalog/{product_id}?err=SKU+{sku_code}+already+exists", status_code=303)

    form = await request.form()
    variant_attrs = _parse_attributes_from_form(form, _active_schema_fields(db, user.tenant_id), prefix="vattr__")

    variant = ProductVariant(
        id=new_id(), tenant_id=user.tenant_id, product_id=product.id,
        sku_code=sku_code, variant_label=_build_variant_label(sku_code, variant_label, variant_attrs),
        variant_attributes_json=json.dumps(variant_attrs),
        base_unit_id=base_unit_id or None,
        low_stock_threshold=float(low_stock_threshold) if low_stock_threshold else None,
        product_tier=product_tier or "UNRANKED",
        is_active=bool(is_active),
        created_by_id=user.id,
    )
    db.add(variant)
    db.flush()
    db.add(ProductStock(variant_id=variant.id, tenant_id=user.tenant_id))
    sync_end_product_from_variant(db, variant)
    db.commit()
    return RedirectResponse(f"/sales/catalog/{product_id}?msg=Variant+added", status_code=303)


@router.get("/sales/catalog/variant/{variant_id}/edit", response_class=HTMLResponse)
def variant_edit_form(variant_id: str, request: Request, user: User = Depends(_require_sales_editor_or_redirect), db: Session = Depends(get_db)):
    variant = get_variant_or_404(db, variant_id, user.tenant_id)
    return templates.TemplateResponse(request, "sales/catalog_variant_edit.html", _ctx(
        db, user, variant=variant, product=variant.product,
        attributes=json.loads(variant.variant_attributes_json or "{}"),
        media_urls=json.loads(variant.media_urls_json or "[]"),
        units=_active_units(db, user.tenant_id),
        tier_choices=TIER_CHOICES,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/sales/catalog/variant/{variant_id}/edit")
async def variant_edit_save(
    variant_id: str,
    request: Request,
    sku_code: str = Form(...),
    variant_label: str = Form(""),
    base_unit_id: str = Form(""),
    low_stock_threshold: str = Form(""),
    product_tier: str = Form("UNRANKED"),
    is_active: Optional[str] = Form(None),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    variant = get_variant_or_404(db, variant_id, user.tenant_id)
    sku_code = sku_code.strip()
    existing = db.query(ProductVariant).filter(
        ProductVariant.tenant_id == user.tenant_id, ProductVariant.sku_code == sku_code,
        ProductVariant.id != variant_id, ProductVariant.is_deleted == False,
    ).first()
    if existing:
        return RedirectResponse(f"/sales/catalog/{variant.product_id}?err=SKU+{sku_code}+already+exists", status_code=303)

    variant.sku_code = sku_code
    variant.variant_label = variant_label.strip() or variant.variant_label
    variant.base_unit_id = base_unit_id or None
    variant.low_stock_threshold = float(low_stock_threshold) if low_stock_threshold else None
    variant.product_tier = product_tier or "UNRANKED"
    variant.is_active = bool(is_active)
    variant.updated_at = datetime.utcnow()
    sync_end_product_from_variant(db, variant)
    db.commit()
    return RedirectResponse(f"/sales/catalog/{variant.product_id}?msg=Variant+updated", status_code=303)


@router.post("/sales/catalog/variant/{variant_id}/delete")
def variant_delete(variant_id: str, user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    variant = get_variant_or_404(db, variant_id, user.tenant_id)
    product_id = variant.product_id
    variant.is_deleted = True
    remove_end_product_for_variant(db, variant)
    db.commit()
    return RedirectResponse(f"/sales/catalog/{product_id}?msg=Variant+removed", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA (per-variant)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sales/catalog/variant/{variant_id}/upload-media")
async def upload_variant_media(
    variant_id: str,
    files: List[UploadFile] = File(...),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    variant = get_variant_or_404(db, variant_id, user.tenant_id)
    existing = json.loads(variant.media_urls_json or "[]")

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
        rel_path = f"uploads/{user.tenant_id}/products/{variant_id}/{filename}"
        full_path = Path(__file__).parent / "static" / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        existing.append(rel_path)

    variant.media_urls_json = json.dumps(existing)
    variant.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/sales/catalog/{variant.product_id}?msg=Photos+uploaded", status_code=303)


@router.post("/sales/catalog/variant/{variant_id}/delete-media")
def delete_variant_media(
    variant_id: str,
    index: int = Form(...),
    user: User = Depends(_require_sales_editor),
    db: Session = Depends(get_db),
):
    variant = get_variant_or_404(db, variant_id, user.tenant_id)
    existing = json.loads(variant.media_urls_json or "[]")
    if 0 <= index < len(existing):
        existing.pop(index)
        variant.media_urls_json = json.dumps(existing)
        variant.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(f"/sales/catalog/{variant.product_id}?msg=Photo+removed", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA MANAGEMENT (Admin only) — unchanged, shared attributes live on Product
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog/schema", response_class=HTMLResponse)
def catalog_schema_page(request: Request, user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
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
# BULK OPERATIONS — one row = one Variant; rows sharing category/sub_category/
# product_name collapse onto the same parent Product.
# ══════════════════════════════════════════════════════════════════════════════

_BASE_VARIANT_COLS = [
    "category", "sub_category", "product_name", "product_description",
    "sku_code", "variant_label", "unit_abbreviation", "low_stock_threshold", "photo_drive_link",
]
# Stock quantities are intentionally NOT part of product creation — seed initial
# stock via the dedicated Bulk Stock In upload (/inventory-v2/stock-in/bulk) or
# the Adjust modal, keyed at SKU + Branch level. Keeps "define a product" and
# "record stock in hand" as separate, unambiguous operations.


@router.get("/sales/catalog/bulk-upload", response_class=HTMLResponse)
def bulk_upload_page(request: Request, user: User = Depends(_require_sales_editor_or_redirect), db: Session = Depends(get_db)):
    schema_fields = _active_schema_fields(db, user.tenant_id)
    cols = _BASE_VARIANT_COLS + [f.label for f in schema_fields]
    return templates.TemplateResponse(request, "sales/catalog_bulk_upload.html", _ctx(db, user, columns=cols))


@router.get("/sales/catalog/bulk-template")
def bulk_template(user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    schema_fields = _active_schema_fields(db, user.tenant_id)
    cols = _BASE_VARIANT_COLS + [f.label for f in schema_fields]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=product_variant_template.csv"},
    )


def _validate_variant_row(row, tenant_id, db, existing_skus):
    errors = []
    sku = (row.get("sku_code") or "").strip()
    # A blank sku_code is fine — one is auto-generated (CC-SSS-####) at import
    # time from the row's category/sub-category, same as Setup's End Products.
    if sku and sku in existing_skus:
        errors.append(f"SKU '{sku}' already exists — use a unique SKU or remove this row.")
    if not (row.get("product_name") or "").strip():
        errors.append("product_name is required — fill in a value for every row.")
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


def _run_variant_validation(rows_in: list, tenant_id: str, db: Session, start_index: int = 2) -> dict:
    existing_skus = {
        r[0] for r in db.query(ProductVariant.sku_code).filter(
            ProductVariant.tenant_id == tenant_id, ProductVariant.is_deleted == False,
        ).all()
    }
    results = []
    valid_rows = []
    seen_in_file = set()
    for i, row in enumerate(rows_in, start=start_index):
        errors = _validate_variant_row(row, tenant_id, db, existing_skus | seen_in_file)
        sku = (row.get("sku_code") or "").strip()
        if not errors:
            valid_rows.append(row)
            seen_in_file.add(sku)
        else:
            results.append({"row": row.get("_row", i), "error": "; ".join(errors), "data": dict(row)})
    return {
        "total": len(valid_rows) + len(results),
        "valid": len(valid_rows),
        "errors": results,
        "rows": valid_rows,
    }


@router.post("/sales/catalog/bulk-upload")
async def bulk_upload(
    file: UploadFile = File(...),
    user: User = Depends(_require_sales_editor),
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
    fmt_err = check_required_headers(dict_reader.fieldnames, ["sku_code", "product_name"], _BASE_VARIANT_COLS)
    if fmt_err:
        return JSONResponse({"format_error": fmt_err})
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(rows)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    for i, row in enumerate(rows, start=2):
        row["_row"] = i
    return JSONResponse(_run_variant_validation(rows, user.tenant_id, db))


@router.post("/sales/catalog/bulk-upload/revalidate")
async def bulk_upload_revalidate(request: Request, user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    body = await request.json()
    rows_in = body.get("rows", [])
    if len(rows_in) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Too many rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    return JSONResponse(_run_variant_validation(rows_in, user.tenant_id, db))


@router.post("/sales/catalog/bulk-upload/confirm")
async def bulk_upload_confirm(request: Request, user: User = Depends(_require_sales_editor), db: Session = Depends(get_db)):
    body = await request.json()
    rows = body.get("rows", [])
    existing_skus = {
        r[0] for r in db.query(ProductVariant.sku_code).filter(
            ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False,
        ).all()
    }

    # Collapse rows sharing (category, sub_category, product_name) onto one parent Product.
    product_cache: dict = {}
    created = 0
    skipped = 0
    photo_warnings = []

    try:
        for row in rows:
            errors = _validate_variant_row(row, user.tenant_id, db, existing_skus)
            if errors:
                skipped += 1
                continue

            cat_name = (row.get("category") or "").strip() or "Uncategorized"
            sub_name = (row.get("sub_category") or "").strip() or "General"
            product_name = (row.get("product_name") or "").strip()
            key = (cat_name, sub_name, product_name)

            if key not in product_cache:
                product_cache[key] = resolve_or_create_hierarchy(
                    db, user.tenant_id, cat_name, sub_name, product_name,
                    row.get("product_description"), user.id,
                )
            product = product_cache[key]

            unit_abbr = (row.get("unit_abbreviation") or "").strip()
            unit = None
            if unit_abbr:
                unit = db.query(UnitOfMeasure).filter(
                    UnitOfMeasure.tenant_id == user.tenant_id,
                    UnitOfMeasure.abbreviation == unit_abbr,
                    UnitOfMeasure.is_active == True,
                ).first()

            sku = row.get("sku_code", "").strip()
            if not sku:
                sku = generate_product_sku(db, user.tenant_id, cat_name, sub_name)
            variant = ProductVariant(
                id=new_id(), tenant_id=user.tenant_id, product_id=product.id,
                sku_code=sku,
                variant_label=(row.get("variant_label") or "").strip() or sku,
                base_unit_id=unit.id if unit else None,
                low_stock_threshold=float(row["low_stock_threshold"]) if (row.get("low_stock_threshold") or "").strip() else None,
                created_by_id=user.id,
            )
            db.add(variant)
            db.flush()
            db.add(ProductStock(variant_id=variant.id, tenant_id=user.tenant_id))
            sync_end_product_from_variant(db, variant)
            db.flush()

            drive_link = (row.get("photo_drive_link") or "").strip()
            if drive_link:
                err = await attach_drive_photo(variant, drive_link)
                if err:
                    photo_warnings.append({"sku": sku, "error": err})

            existing_skus.add(sku)
            created += 1

        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no products were created. {e}")
    warnings = [f"Photo for {w['sku']}: {w['error']}" for w in photo_warnings]
    return JSONResponse({"created": created, "skipped": skipped, "photo_warnings": photo_warnings, "warnings": warnings})


@router.get("/sales/catalog/export")
def catalog_export(user: User = Depends(_require_sales), db: Session = Depends(get_db)):
    schema_fields = _active_schema_fields(db, user.tenant_id)
    variants = db.query(ProductVariant).join(Product, ProductVariant.product_id == Product.id).filter(
        ProductVariant.tenant_id == user.tenant_id, ProductVariant.is_deleted == False,
    ).order_by(Product.name, ProductVariant.sku_code).all()

    cols = ["category", "sub_category", "product_name", "sku_code", "variant_label", "unit", "tier", "is_active", "low_stock_threshold"]
    cols += [f.label for f in schema_fields]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    for v in variants:
        p = v.product
        attrs = json.loads(p.attributes_json or "{}") if p else {}
        sub = p.sub_category if p else None
        cat = sub.category if sub else None
        unit = v.base_unit or (p.base_unit if p else None)
        row = [
            cat.name if cat else "", sub.name if sub else "", p.name if p else "",
            v.sku_code, v.variant_label or "", unit.abbreviation if unit else "",
            v.product_tier, v.is_active, v.low_stock_threshold or "",
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
# paths above (categories, schema, bulk-upload, bulk-template, export, variant).
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/catalog/{product_id}", response_class=HTMLResponse)
def catalog_detail(product_id: str, request: Request, user: User = Depends(_require_sales_or_redirect), db: Session = Depends(get_db)):
    product = get_product_or_404(db, product_id, user.tenant_id)
    variants = db.query(ProductVariant).filter(
        ProductVariant.product_id == product_id, ProductVariant.is_deleted == False,
    ).order_by(ProductVariant.created_at).all()

    stock_by_variant = {
        s.variant_id: s for s in db.query(ProductStock).filter(
            ProductStock.tenant_id == user.tenant_id,
            ProductStock.variant_id.in_([v.id for v in variants]),
            ProductStock.branch_id.is_(None),
        ).all()
    } if variants else {}

    # Setup <-> Sales cross-link (Phase 5): match on sku_code, the same key
    # sales_catalog_sync.py already uses to keep the two in sync.
    skus = [v.sku_code for v in variants if v.sku_code]
    setup_end_product = db.query(EndProduct).filter(
        EndProduct.tenant_id == user.tenant_id,
        EndProduct.sku_code.in_(skus),
        EndProduct.is_deleted == False,
    ).first() if skus else None

    # Redesign (2026-07): prev/next product navigation on the detail page,
    # scoped to the tenant's active product ordering (by name) — mirrors the
    # design mock's ‹ › browse controls.
    sibling_ids = [
        row[0] for row in db.query(Product.id).filter(
            Product.tenant_id == user.tenant_id, Product.is_deleted == False,
        ).order_by(Product.name).all()
    ]
    prev_product_id = next_product_id = None
    if product_id in sibling_ids:
        idx = sibling_ids.index(product_id)
        prev_product_id = sibling_ids[idx - 1]
        next_product_id = sibling_ids[(idx + 1) % len(sibling_ids)]

    total_stock = sum(
        (stock_by_variant.get(v.id).qty_available if stock_by_variant.get(v.id) else 0) or 0
        for v in variants
    )

    return templates.TemplateResponse(request, "sales/catalog_detail.html", _ctx(
        db, user,
        product=product,
        attributes=json.loads(product.attributes_json or "{}"),
        variants=variants,
        stock_by_variant=stock_by_variant,
        total_stock=total_stock,
        units=_active_units(db, user.tenant_id),
        schema_fields=_active_schema_fields(db, user.tenant_id),
        categories=_active_categories(db, user.tenant_id),
        subcats_by_cat={sc.id: sc for sc in _active_subcategories(db, user.tenant_id)},
        tier_choices=TIER_CHOICES,
        setup_end_product=setup_end_product,
        prev_product_id=prev_product_id,
        next_product_id=next_product_id,
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))
