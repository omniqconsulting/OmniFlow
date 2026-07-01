"""
Phase 2 — Setup Module: Customers, End Products, Custom Lists, Org Chart,
Deployed Config, and Inventory Reference routes.
"""
from __future__ import annotations

import csv, io, json, re

_PHONE_RE = re.compile(r'^[0-9+\-\s()]{7,20}$')
from datetime import datetime, date as _date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from markupsafe import Markup
from sqlalchemy.orm import Session
import os

from .database import (
    get_db, new_id,
    User, Tenant, Branch, Department,
    Customer, EndProduct, Vendor, RawMaterial,
    CustomReferenceList, CustomReferenceItem,
    FMSFlow, FMSStage, FMSTicket, FMSStageHistory,
)
from .auth import require_admin
from .labels import get_labels

router = APIRouter()

from .templates_env import templates  # shared instance — has all filters

import json as _json
from markupsafe import Markup as _Markup


def _build_ref_lists_json(tenant_id: str, db) -> str:
    """Return JSON array of all selectable lists: system entity tables + custom reference lists."""
    result = []

    # ── System entity tables ──────────────────────────────────────────────────
    _sys = [
        ("__system_customer__",    "Customers",      Customer,     "name"),
        ("__system_vendor__",      "Vendors",        Vendor,       "name"),
        ("__system_rawmaterial__", "Raw Materials",  RawMaterial,  "name"),
        ("__system_endproduct__",  "End Products",   EndProduct,   "name"),
        ("__system_department__",  "Departments",    Department,   "name"),
        ("__system_branch__",      "Branches",       Branch,       "name"),
    ]
    for sys_id, sys_name, model, name_col in _sys:
        rows = db.query(model).filter(
            model.tenant_id == tenant_id,
            model.is_deleted == False,
        ).order_by(getattr(model, name_col)).all()
        items = [getattr(r, name_col) for r in rows if getattr(r, name_col, None)]
        if items:
            result.append({"id": sys_id, "name": sys_name, "items": items, "system": True})

    # ── Custom reference lists ────────────────────────────────────────────────
    custom = db.query(CustomReferenceList).filter(
        CustomReferenceList.tenant_id == tenant_id,
        CustomReferenceList.is_deleted == False,
        CustomReferenceList.is_active != False,
    ).order_by(CustomReferenceList.list_name).all()
    for l in custom:
        items = [i.value for i in l.items if i.is_active and not i.is_deleted]
        result.append({"id": l.id, "name": l.list_name, "items": items, "system": False})

    return _json.dumps(result)


def _redir(path: str):
    return RedirectResponse(path, status_code=302)


def _L(db: Session, user: User):
    return get_labels(db, user.tenant_id)


def _unread(db: Session, user: User) -> int:
    from .database import Notification
    return db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False,
    ).count()


def _nav_ctx(db: Session, user: User) -> dict:
    from .constants import has_feature
    from .auth import get_user_modules
    tenant = db.query(Tenant).get(user.tenant_id)
    modules = get_user_modules(user)
    return {
        "has_inventory":         has_feature(tenant, "INVENTORY",       db),
        "has_fms":               has_feature(tenant, "FMS",             db),
        "has_knowledge_repo":    has_feature(tenant, "KNOWLEDGE_REPO",  db),
        "has_checklists":        has_feature(tenant, "CHECKLISTS",      db),
        "has_ai":                has_feature(tenant, "ASK_AI",          db),
        "has_sales":             "SALES"     in modules and has_feature(tenant, "SALES_MODULE",     db),
        "has_inventory_module":  "INVENTORY" in modules and has_feature(tenant, "INVENTORY_MODULE",  db),
        "has_sales_analytics":   has_feature(tenant, "SALES_ANALYTICS", db)
                                  and has_feature(tenant, "SALES_MODULE", db)
                                  and "SALES" in modules and user.role in ("ADMIN", "MANAGER"),
        "user_modules":          modules,
    }


# ── Customer CSV template columns ─────────────────────────────────────────────
_CUSTOMER_COLS = [
    ("name",           "Mandatory. Customer/client name. Max 200 characters."),
    ("contact_person", "Optional. Primary contact name at the customer."),
    ("phone",          "Optional. Contact phone number."),
    ("email",          "Optional. Contact email address."),
    ("address",        "Optional. Customer address. Free text."),
    ("notes",          "Optional. Any additional notes. Free text."),
]

_ENDPRODUCT_COLS = [
    ("name",        "Mandatory. Product name. Max 200 characters."),
    ("sku_code",    "Optional. Must be unique per tenant if provided. Alphanumeric, no spaces."),
    ("unit",        "Optional. Unit of measure. E.g. pcs, kg, litres, box."),
    ("description", "Optional. Product description. Free text."),
]

_CUSTOM_ITEM_COLS = [
    ("list_name",   "Mandatory. Must match an existing Custom List name exactly. Case-sensitive."),
    ("value",       "Mandatory. The item label to show in the dropdown. Max 200 characters."),
    ("sort_order",  "Optional. Integer. Determines display order. Defaults to 0."),
]

_VENDOR_COLS = [
    ("name",           "Mandatory. Vendor / supplier name. Max 200 characters."),
    ("contact_person", "Optional. Primary contact name at the vendor."),
    ("phone",          "Optional. Contact phone. Numbers, +, -, spaces and () only, 7–20 characters."),
    ("email",          "Optional. Contact email address."),
    ("address",        "Optional. Vendor address. Free text."),
    ("parts_supplied", "Optional. Comma-separated list of parts or materials supplied by this vendor."),
    ("notes",          "Optional. Any additional notes. Free text."),
]

_RAW_MATERIAL_COLS = [
    ("name",           "Mandatory. Raw material name. Max 200 characters."),
    ("unit",           "Optional. Unit of measure. E.g. kg, pcs, litres, box."),
    ("description",    "Optional. Material description. Free text."),
    ("major_supplier", "Optional. Primary supplier name for this material."),
    ("notes",          "Optional. Any additional notes. Free text."),
]


def _csv_template(rows: list[tuple[str, str]], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([r[0] for r in rows])
    w.writerow([r[1] for r in rows])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _exception_report(errors: list[dict], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["row", "error", "data"])
    w.writeheader()
    for e in errors:
        w.writerow({"row": e["row"], "error": e["error"], "data": str(e.get("data", ""))})
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMERS
# ══════════════════════════════════════════════════════════════════════════════

PAGE_SIZE = 20


@router.get("/setup/customers", response_class=HTMLResponse)
def customers_page(
    request: Request,
    page: int = 1,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Customer).filter(
        Customer.tenant_id == user.tenant_id,
        Customer.is_deleted == False,
    ).order_by(Customer.name)
    total = q.count()
    customers = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    msg = request.query_params.get("msg", "")
    err = request.query_params.get("err", "")
    return templates.TemplateResponse(request, "setup/customers.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "customers": customers, "total": total,
        "page": page, "page_size": PAGE_SIZE,
        "msg": msg, "err": err,
    })


@router.post("/setup/customers/add")
def add_customer(
    name: str = Form(...),
    contact_person: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return _redir("/setup/customers?err=Name+is+required")
    p = phone.strip()
    if p and not _PHONE_RE.match(p):
        return _redir("/setup/customers?err=Invalid+phone+number+format")
    db.add(Customer(
        tenant_id=user.tenant_id,
        name=name.strip(), contact_person=contact_person.strip() or None,
        phone=p or None, email=email.strip() or None,
        address=address.strip() or None, notes=notes.strip() or None,
        created_by_id=user.id,
    ))
    db.commit()
    return _redir("/setup/customers?msg=Customer+added")


@router.post("/setup/customers/{cust_id}/edit")
def edit_customer(
    cust_id: str,
    name: str = Form(...),
    contact_person: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    is_active: str = Form("1"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    c = db.query(Customer).filter(
        Customer.id == cust_id, Customer.tenant_id == user.tenant_id,
        Customer.is_deleted == False,
    ).first()
    if not c:
        return _redir("/setup/customers?err=Not+found")
    p = phone.strip()
    if p and not _PHONE_RE.match(p):
        return _redir("/setup/customers?err=Invalid+phone+number+format")
    c.name = name.strip()
    c.contact_person = contact_person.strip() or None
    c.phone = p or None
    c.email = email.strip() or None
    c.address = address.strip() or None
    c.notes = notes.strip() or None
    c.is_active = is_active == "1"
    c.updated_at = datetime.utcnow()
    db.commit()
    return _redir("/setup/customers?msg=Customer+updated")


@router.post("/setup/customers/{cust_id}/delete")
def delete_customer(
    cust_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    c = db.query(Customer).filter(
        Customer.id == cust_id, Customer.tenant_id == user.tenant_id,
    ).first()
    if c:
        c.is_deleted = True
        db.commit()
    return _redir("/setup/customers?msg=Customer+deleted")


@router.get("/setup/customers/template")
def customers_template(user: User = Depends(require_admin)):
    return _csv_template(_CUSTOMER_COLS, "customers_template.csv")


@router.post("/setup/customers/import")
async def import_customers(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    errors, imported = [], 0
    for i, row in enumerate(reader, start=2):
        # Skip description row
        if row.get("name", "").startswith("Mandatory") or row.get("name", "").startswith("Optional"):
            continue
        name = (row.get("name") or "").strip()
        if not name:
            errors.append({"row": i, "error": "name is required", "data": dict(row)})
            continue
        if len(name) > 200:
            errors.append({"row": i, "error": "name exceeds 200 characters", "data": dict(row)})
            continue
        db.add(Customer(
            tenant_id=user.tenant_id,
            name=name,
            contact_person=(row.get("contact_person") or "").strip() or None,
            phone=(row.get("phone") or "").strip() or None,
            email=(row.get("email") or "").strip() or None,
            address=(row.get("address") or "").strip() or None,
            notes=(row.get("notes") or "").strip() or None,
            created_by_id=user.id,
        ))
        imported += 1

    db.commit()
    if errors:
        r = _exception_report(errors, "customers_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/customers?msg=Imported+{imported}+customer(s)")


# ══════════════════════════════════════════════════════════════════════════════
# END PRODUCTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/end-products", response_class=HTMLResponse)
def end_products_page(
    request: Request,
    page: int = 1,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(EndProduct).filter(
        EndProduct.tenant_id == user.tenant_id,
        EndProduct.is_deleted == False,
    ).order_by(EndProduct.name)
    total = q.count()
    products = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return templates.TemplateResponse(request, "setup/end_products.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "products": products, "total": total,
        "page": page, "page_size": PAGE_SIZE,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


@router.post("/setup/end-products/add")
def add_end_product(
    name: str = Form(...),
    sku_code: str = Form(""),
    unit: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return _redir("/setup/end-products?err=Name+is+required")
    sku = sku_code.strip() or None
    if sku:
        exists = db.query(EndProduct).filter(
            EndProduct.tenant_id == user.tenant_id,
            EndProduct.sku_code == sku,
            EndProduct.is_deleted == False,
        ).first()
        if exists:
            return _redir(f"/setup/end-products?err=SKU+{sku}+already+exists")
    db.add(EndProduct(
        tenant_id=user.tenant_id, name=name.strip(), sku_code=sku,
        unit=unit.strip() or None, description=description.strip() or None,
        created_by_id=user.id,
    ))
    db.commit()
    return _redir("/setup/end-products?msg=Product+added")


@router.post("/setup/end-products/{prod_id}/edit")
def edit_end_product(
    prod_id: str,
    name: str = Form(...),
    sku_code: str = Form(""),
    unit: str = Form(""),
    description: str = Form(""),
    is_active: str = Form("1"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = db.query(EndProduct).filter(
        EndProduct.id == prod_id, EndProduct.tenant_id == user.tenant_id,
        EndProduct.is_deleted == False,
    ).first()
    if not p:
        return _redir("/setup/end-products?err=Not+found")
    sku = sku_code.strip() or None
    if sku and sku != p.sku_code:
        exists = db.query(EndProduct).filter(
            EndProduct.tenant_id == user.tenant_id,
            EndProduct.sku_code == sku,
            EndProduct.id != prod_id,
            EndProduct.is_deleted == False,
        ).first()
        if exists:
            return _redir(f"/setup/end-products?err=SKU+{sku}+already+exists")
    p.name = name.strip()
    p.sku_code = sku
    p.unit = unit.strip() or None
    p.description = description.strip() or None
    p.is_active = is_active == "1"
    p.updated_at = datetime.utcnow()
    db.commit()
    return _redir("/setup/end-products?msg=Product+updated")


@router.post("/setup/end-products/{prod_id}/delete")
def delete_end_product(
    prod_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = db.query(EndProduct).filter(
        EndProduct.id == prod_id, EndProduct.tenant_id == user.tenant_id,
    ).first()
    if p:
        p.is_deleted = True
        db.commit()
    return _redir("/setup/end-products?msg=Product+deleted")


@router.get("/setup/end-products/template")
def end_products_template(user: User = Depends(require_admin)):
    return _csv_template(_ENDPRODUCT_COLS, "end_products_template.csv")


@router.post("/setup/end-products/import")
async def import_end_products(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    errors, imported = [], 0
    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        if not name or name.startswith("Mandatory"):
            if not name:
                errors.append({"row": i, "error": "name is required", "data": dict(row)})
            continue
        sku = (row.get("sku_code") or "").strip() or None
        if sku:
            exists = db.query(EndProduct).filter(
                EndProduct.tenant_id == user.tenant_id,
                EndProduct.sku_code == sku, EndProduct.is_deleted == False,
            ).first()
            if exists:
                errors.append({"row": i, "error": f"SKU {sku} already exists", "data": dict(row)})
                continue
        db.add(EndProduct(
            tenant_id=user.tenant_id, name=name, sku_code=sku,
            unit=(row.get("unit") or "").strip() or None,
            description=(row.get("description") or "").strip() or None,
            created_by_id=user.id,
        ))
        imported += 1
    db.commit()
    if errors:
        r = _exception_report(errors, "end_products_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/end-products?msg=Imported+{imported}+product(s)")


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOM REFERENCE LISTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/custom-lists", response_class=HTMLResponse)
def custom_lists_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lists = db.query(CustomReferenceList).filter(
        CustomReferenceList.tenant_id == user.tenant_id,
        CustomReferenceList.is_deleted == False,
    ).order_by(CustomReferenceList.list_name).all()
    return templates.TemplateResponse(request, "setup/custom_lists.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "lists": lists,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


@router.get("/setup/how-to", response_class=HTMLResponse)
def howto_page(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "setup/howto.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
    })


@router.post("/setup/custom-lists/add")
def add_custom_list(
    list_name: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not list_name.strip():
        return _redir("/setup/custom-lists?err=List+name+is+required")
    db.add(CustomReferenceList(
        tenant_id=user.tenant_id, list_name=list_name.strip(),
        created_by_id=user.id,
    ))
    db.commit()
    return _redir("/setup/custom-lists?msg=List+created")


@router.post("/setup/custom-lists/{list_id}/edit")
def edit_custom_list(
    list_id: str,
    list_name: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lst = db.query(CustomReferenceList).filter(
        CustomReferenceList.id == list_id,
        CustomReferenceList.tenant_id == user.tenant_id,
        CustomReferenceList.is_deleted == False,
    ).first()
    if lst:
        lst.list_name = list_name.strip()
        db.commit()
    return _redir("/setup/custom-lists?msg=List+renamed")


@router.post("/setup/custom-lists/{list_id}/delete")
def delete_custom_list(
    list_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lst = db.query(CustomReferenceList).filter(
        CustomReferenceList.id == list_id,
        CustomReferenceList.tenant_id == user.tenant_id,
    ).first()
    if lst:
        lst.is_deleted = True
        for item in lst.items:
            item.is_deleted = True
        db.commit()
    return _redir("/setup/custom-lists?msg=List+deleted")


@router.post("/setup/custom-lists/{list_id}/items/add")
def add_list_item(
    list_id: str,
    value: str = Form(...),
    sort_order: int = Form(0),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lst = db.query(CustomReferenceList).filter(
        CustomReferenceList.id == list_id,
        CustomReferenceList.tenant_id == user.tenant_id,
        CustomReferenceList.is_deleted == False,
    ).first()
    if not lst:
        return _redir("/setup/custom-lists?err=List+not+found")
    db.add(CustomReferenceItem(
        list_id=list_id, tenant_id=user.tenant_id,
        value=value.strip(), sort_order=sort_order,
    ))
    db.commit()
    return _redir("/setup/custom-lists?msg=Item+added")


@router.post("/setup/custom-lists/items/{item_id}/delete")
def delete_list_item(
    item_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(CustomReferenceItem).filter(
        CustomReferenceItem.id == item_id,
        CustomReferenceItem.tenant_id == user.tenant_id,
    ).first()
    if item:
        item.is_deleted = True
        db.commit()
    return _redir("/setup/custom-lists?msg=Item+removed")


@router.get("/setup/custom-lists/items/template")
def custom_items_template(user: User = Depends(require_admin)):
    return _csv_template(_CUSTOM_ITEM_COLS, "custom_list_items_template.csv")


@router.post("/setup/custom-lists/items/import")
async def import_list_items(
    file: UploadFile = File(...),
    list_id: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    errors, imported = [], 0
    for i, row in enumerate(reader, start=2):
        list_name = (row.get("list_name") or "").strip()
        value = (row.get("value") or "").strip()
        if not value or value.startswith("Mandatory"):
            if not value:
                errors.append({"row": i, "error": "value is required", "data": dict(row)})
            continue

        # resolve list by name or use provided list_id
        target_list = None
        if list_name:
            target_list = db.query(CustomReferenceList).filter(
                CustomReferenceList.tenant_id == user.tenant_id,
                CustomReferenceList.list_name == list_name,
                CustomReferenceList.is_deleted == False,
            ).first()
            if not target_list:
                errors.append({"row": i, "error": f"List '{list_name}' not found", "data": dict(row)})
                continue
        elif list_id:
            target_list = db.query(CustomReferenceList).filter(
                CustomReferenceList.id == list_id,
                CustomReferenceList.tenant_id == user.tenant_id,
            ).first()

        if not target_list:
            errors.append({"row": i, "error": "list_name is required or list not found", "data": dict(row)})
            continue

        try:
            sort_order = int((row.get("sort_order") or "0").strip())
        except ValueError:
            sort_order = 0

        db.add(CustomReferenceItem(
            list_id=target_list.id, tenant_id=user.tenant_id,
            value=value, sort_order=sort_order,
        ))
        imported += 1

    db.commit()
    if errors:
        r = _exception_report(errors, "custom_items_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/custom-lists?msg=Imported+{imported}+item(s)")


# ══════════════════════════════════════════════════════════════════════════════
# DEPLOYED CONFIGURATION (read-only)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/deployed-config", response_class=HTMLResponse)
def deployed_config_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    flows_raw = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_active == True,
        FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.name).all()

    flows = []
    for f in flows_raw:
        active_count = db.query(FMSTicket).filter(
            FMSTicket.flow_id == f.id,
            FMSTicket.is_deleted == False,
            FMSTicket.status.in_(["ACTIVE", "STAGE_COMPLETE", "IN_TRANSITION"]),
        ).count()
        flows.append({
            "name": f.name, "stages": f.stages,
            "active_tickets": active_count, "created_at": f.created_at,
        })

    return templates.TemplateResponse(request, "setup/deployed_config.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "flows": flows,
    })


# ══════════════════════════════════════════════════════════════════════════════
# VENDORS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/vendors", response_class=HTMLResponse)
def vendors_page(request: Request, page: int = 1,
                 user: User = Depends(require_admin), db: Session = Depends(get_db)):
    q = db.query(Vendor).filter(Vendor.tenant_id == user.tenant_id, Vendor.is_deleted == False).order_by(Vendor.name)
    total = q.count()
    vendors = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return templates.TemplateResponse(request, "setup/vendors.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "vendors": vendors, "total": total, "page": page, "page_size": PAGE_SIZE,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })

@router.post("/setup/vendors/add")
def add_vendor(name: str = Form(...), contact_person: str = Form(""), phone: str = Form(""),
               email: str = Form(""), address: str = Form(""), parts_supplied: str = Form(""),
               notes: str = Form(""),
               user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not name.strip():
        return _redir("/setup/vendors?err=Name+is+required")
    p = phone.strip()
    if p and not _PHONE_RE.match(p):
        return _redir("/setup/vendors?err=Invalid+phone+number+format")
    db.add(Vendor(tenant_id=user.tenant_id, name=name.strip(),
                  contact_person=contact_person.strip() or None,
                  phone=p or None, email=email.strip() or None,
                  address=address.strip() or None,
                  parts_supplied=parts_supplied.strip() or None,
                  notes=notes.strip() or None,
                  created_by_id=user.id))
    db.commit()
    return _redir("/setup/vendors?msg=Vendor+added")

@router.post("/setup/vendors/{vendor_id}/edit")
def edit_vendor(vendor_id: str, name: str = Form(...), contact_person: str = Form(""),
                phone: str = Form(""), email: str = Form(""), address: str = Form(""),
                parts_supplied: str = Form(""), notes: str = Form(""), is_active: str = Form("1"),
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.tenant_id == user.tenant_id, Vendor.is_deleted == False).first()
    if not v:
        return _redir("/setup/vendors?err=Not+found")
    p = phone.strip()
    if p and not _PHONE_RE.match(p):
        return _redir("/setup/vendors?err=Invalid+phone+number+format")
    v.name = name.strip(); v.contact_person = contact_person.strip() or None
    v.phone = p or None; v.email = email.strip() or None
    v.address = address.strip() or None
    v.parts_supplied = parts_supplied.strip() or None
    v.notes = notes.strip() or None
    v.is_active = (is_active == "1"); v.updated_at = datetime.utcnow()
    db.commit()
    return _redir("/setup/vendors?msg=Vendor+updated")

@router.post("/setup/vendors/{vendor_id}/delete")
def delete_vendor(vendor_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.tenant_id == user.tenant_id).first()
    if v:
        v.is_deleted = True; db.commit()
    return _redir("/setup/vendors?msg=Vendor+deleted")

@router.get("/setup/vendors/template")
def vendors_template(user: User = Depends(require_admin)):
    return _csv_template(_VENDOR_COLS, "vendors_template.csv")

@router.post("/setup/vendors/import")
async def import_vendors(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    errors, imported = [], 0
    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        if not name or name.startswith("Mandatory") or name.startswith("Optional"):
            if name and not name.startswith("Mandatory"):
                errors.append({"row": i, "error": "name is required", "data": dict(row)})
            continue
        if len(name) > 200:
            errors.append({"row": i, "error": "name exceeds 200 characters", "data": dict(row)})
            continue
        phone = (row.get("phone") or "").strip()
        if phone and not _PHONE_RE.match(phone):
            errors.append({"row": i, "error": "invalid phone number format", "data": dict(row)})
            continue
        db.add(Vendor(
            tenant_id=user.tenant_id,
            name=name,
            contact_person=(row.get("contact_person") or "").strip() or None,
            phone=phone or None,
            email=(row.get("email") or "").strip() or None,
            address=(row.get("address") or "").strip() or None,
            parts_supplied=(row.get("parts_supplied") or "").strip() or None,
            notes=(row.get("notes") or "").strip() or None,
            created_by_id=user.id,
        ))
        imported += 1
    db.commit()
    if errors:
        r = _exception_report(errors, "vendors_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/vendors?msg=Imported+{imported}+vendor(s)")


# ══════════════════════════════════════════════════════════════════════════════
# RAW MATERIALS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/raw-materials", response_class=HTMLResponse)
def raw_materials_page(request: Request, page: int = 1,
                       user: User = Depends(require_admin), db: Session = Depends(get_db)):
    q = db.query(RawMaterial).filter(RawMaterial.tenant_id == user.tenant_id, RawMaterial.is_deleted == False).order_by(RawMaterial.name)
    total = q.count()
    items = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return templates.TemplateResponse(request, "setup/raw_materials.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "items": items, "total": total, "page": page, "page_size": PAGE_SIZE,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })

@router.post("/setup/raw-materials/add")
def add_raw_material(name: str = Form(...), unit: str = Form(""), description: str = Form(""),
                     major_supplier: str = Form(""), notes: str = Form(""),
                     user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not name.strip():
        return _redir("/setup/raw-materials?err=Name+is+required")
    db.add(RawMaterial(tenant_id=user.tenant_id, name=name.strip(),
                       unit=unit.strip() or None, description=description.strip() or None,
                       major_supplier=major_supplier.strip() or None,
                       notes=notes.strip() or None, created_by_id=user.id))
    db.commit()
    return _redir("/setup/raw-materials?msg=Raw+material+added")

@router.post("/setup/raw-materials/{item_id}/edit")
def edit_raw_material(item_id: str, name: str = Form(...), unit: str = Form(""),
                      description: str = Form(""), major_supplier: str = Form(""),
                      notes: str = Form(""), is_active: str = Form("1"),
                      user: User = Depends(require_admin), db: Session = Depends(get_db)):
    m = db.query(RawMaterial).filter(RawMaterial.id == item_id, RawMaterial.tenant_id == user.tenant_id, RawMaterial.is_deleted == False).first()
    if not m:
        return _redir("/setup/raw-materials?err=Not+found")
    m.name = name.strip(); m.unit = unit.strip() or None
    m.description = description.strip() or None
    m.major_supplier = major_supplier.strip() or None
    m.notes = notes.strip() or None
    m.is_active = (is_active == "1"); m.updated_at = datetime.utcnow()
    db.commit()
    return _redir("/setup/raw-materials?msg=Raw+material+updated")

@router.post("/setup/raw-materials/{item_id}/delete")
def delete_raw_material(item_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    m = db.query(RawMaterial).filter(RawMaterial.id == item_id, RawMaterial.tenant_id == user.tenant_id).first()
    if m:
        m.is_deleted = True; db.commit()
    return _redir("/setup/raw-materials?msg=Raw+material+deleted")

@router.get("/setup/raw-materials/template")
def raw_materials_template(user: User = Depends(require_admin)):
    return _csv_template(_RAW_MATERIAL_COLS, "raw_materials_template.csv")

@router.post("/setup/raw-materials/import")
async def import_raw_materials(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    errors, imported = [], 0
    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        if not name or name.startswith("Mandatory") or name.startswith("Optional"):
            if name and not name.startswith("Mandatory"):
                errors.append({"row": i, "error": "name is required", "data": dict(row)})
            continue
        if len(name) > 200:
            errors.append({"row": i, "error": "name exceeds 200 characters", "data": dict(row)})
            continue
        db.add(RawMaterial(
            tenant_id=user.tenant_id,
            name=name,
            unit=(row.get("unit") or "").strip() or None,
            description=(row.get("description") or "").strip() or None,
            major_supplier=(row.get("major_supplier") or "").strip() or None,
            notes=(row.get("notes") or "").strip() or None,
            created_by_id=user.id,
        ))
        imported += 1
    db.commit()
    if errors:
        r = _exception_report(errors, "raw_materials_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/raw-materials?msg=Imported+{imported}+item(s)")


# ══════════════════════════════════════════════════════════════════════════════
# ORG CHART
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/org-chart", response_class=HTMLResponse)
def org_chart_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    employees = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.is_deleted == False,
        User.is_active == True,
    ).order_by(User.name).all()

    managers = [e for e in employees if e.role in ("MANAGER", "ADMIN")]
    departments = db.query(Department).filter(
        Department.tenant_id == user.tenant_id,
        Department.is_deleted == False,
    ).all()
    branches = db.query(Branch).filter(
        Branch.tenant_id == user.tenant_id,
        Branch.is_deleted == False,
    ).all()

    employees_json = _json.dumps([
        {
            "id": e.id,
            "name": e.name,
            "employee_id": e.employee_id or "",
            "role": e.role,
            "department": e.department.name if e.department else "",
            "department_id": e.department_id or "",
            "branch_id": e.branch_id or "",
            "manager_id": e.manager_id or "",
        }
        for e in employees
    ])

    return templates.TemplateResponse(request, "setup/org_chart.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "managers": managers, "departments": departments, "branches": branches,
        "employees_json": employees_json,
    })


# ══════════════════════════════════════════════════════════════════════════════
# SETUP /employees shortcut — redirects to main employees page
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/employees")
def setup_employees_redirect(user: User = Depends(require_admin)):
    return _redir("/employees")

# ══════════════════════════════════════════════════════════════════════════════
# SETUP FLOWS — Client Flow Builder (Phase A4)
# ══════════════════════════════════════════════════════════════════════════════

import json as _json_flows


def _get_active_employees(db: Session, tenant_id: str):
    return db.query(User).filter(
        User.tenant_id == tenant_id,
        User.is_deleted == False,
        User.is_active == True,
    ).order_by(User.name).all()


def _clean_ticket_form_fields(fields: list) -> list:
    """Validate/normalize ticket-creation-form field defs submitted from the
    flow builder (shared by flow create/update and the dedicated ticket-form
    save endpoint so both paths persist identically-shaped data)."""
    from .database import new_id
    valid_types = {"text", "number", "date", "longtext", "select", "ref_list", "__priority__", "__due_date__"}
    builtin_types = {"__priority__", "__due_date__"}
    clean = []
    for f in fields:
        ftype = (f.get("field_type") or "text").strip().lower()
        label = (f.get("label") or "").strip()
        if not label or ftype not in valid_types:
            continue
        field_id = ftype if ftype in builtin_types else (f.get("id") or new_id())
        entry = {
            "id": field_id,
            "label": label,
            "field_type": ftype,
            "required": bool(f.get("required", False)),
            "order": int(f.get("order", len(clean))),
        }
        if ftype == "select":
            raw_opts = f.get("options", [])
            entry["options"] = [o.strip() for o in raw_opts if str(o).strip()]
        elif ftype == "ref_list":
            entry["ref_list_id"]   = (f.get("ref_list_id") or "").strip()
            entry["ref_list_name"] = (f.get("ref_list_name") or "").strip()
        clean.append(entry)
    return clean


@router.get("/setup/flows", response_class=HTMLResponse)
def setup_flows_list(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .constants import has_feature, PLAN_LIMITS
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "FMS", db):
        return _redir("/setup?err=FMS+not+enabled")

    flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.created_at).all()

    plan = tenant.plan or "STARTER"
    max_flows = PLAN_LIMITS.get(plan, {}).get("max_fms_flows")
    at_limit = max_flows is not None and len(flows) >= max_flows

    flow_info = []
    for f in flows:
        stage_count = len([s for s in f.stages if not s.is_deleted])
        active_tickets = db.query(FMSTicket).filter(
            FMSTicket.flow_id == f.id,
            FMSTicket.is_deleted == False,
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        ).count()
        flow_info.append({
            "flow": f,
            "stage_count": stage_count,
            "active_tickets": active_tickets,
            "can_delete": active_tickets == 0,
        })

    return templates.TemplateResponse(request, "setup/flows.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "flow_info": flow_info,
        "at_limit": at_limit,
        "max_flows": max_flows,
        "active_section": "flows",
    })


@router.get("/setup/flows/new", response_class=HTMLResponse)
def setup_flow_new(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .constants import has_feature, PLAN_LIMITS
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "FMS", db):
        return _redir("/setup?err=FMS+not+enabled")

    plan = tenant.plan or "STARTER"
    max_flows = PLAN_LIMITS.get(plan, {}).get("max_fms_flows")
    current = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).count()
    if max_flows is not None and current >= max_flows:
        return _redir("/setup/flows?err=Flow+limit+reached+for+your+plan")

    employees = _get_active_employees(db, user.tenant_id)
    ref_lists_json = _build_ref_lists_json(user.tenant_id, db)
    return templates.TemplateResponse(request, "setup/flow_edit.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "flow": None,
        "stages_json": "[]",
        "employees": employees,
        "active_section": "flows",
        "ref_lists": _json.loads(ref_lists_json),
        "ref_lists_json": ref_lists_json,
        "ticket_form_fields_json": "[]",
    })


@router.get("/setup/flows/{flow_id}/edit", response_class=HTMLResponse)
def setup_flow_edit_get(
    flow_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id,
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).first()
    if not flow:
        return _redir("/setup/flows?err=Flow+not+found")

    stages = sorted([s for s in flow.stages if not s.is_deleted], key=lambda s: s.order)
    import json as _jf
    stages_data = []
    for s in stages:
        try:
            cf = _jf.loads(s.custom_fields_json or "[]")
        except Exception:
            cf = []
        stages_data.append({
            "id": s.id,
            "name": s.name,
            "order": s.order,
            "color": s.color or "#3b82f6",
            "default_assignee_id": s.default_assignee_id or "",
            "target_tat_hours": s.target_tat_hours,
            "target_tat_unit": s.target_tat_unit or "hours",
            "completion_note_required": s.completion_note_required,
            "is_terminal": s.is_terminal,
            "custom_fields": cf,
        })

    employees = _get_active_employees(db, user.tenant_id)
    ref_lists_json = _build_ref_lists_json(user.tenant_id, db)
    ticket_form_fields = _jf.loads(flow.ticket_form_fields_json or "[]")
    return templates.TemplateResponse(request, "setup/flow_edit.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "flow": flow,
        "stages_json": _jf.dumps(stages_data),
        "employees": employees,
        "active_section": "flows",
        "ref_lists": _json.loads(ref_lists_json),
        "ref_lists_json": ref_lists_json,
        "ticket_form_fields": ticket_form_fields,
        "ticket_form_fields_json": _jf.dumps(ticket_form_fields),
    })


@router.post("/setup/flows/new")
async def setup_flow_create(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from .constants import has_feature, PLAN_LIMITS
    import json as _jf
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "FMS", db):
        return _redir("/setup/flows?err=FMS+not+enabled")

    form = await request.form()
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    color = (form.get("color") or "#3b82f6").strip()
    is_active = form.get("is_active") == "1"
    stages_json_raw = (form.get("stages_json") or "[]").strip()
    ticket_form_fields_json_raw = (form.get("ticket_form_fields_json") or "[]").strip()

    if not name:
        return _redir("/setup/flows/new?err=Flow+name+is+required")

    plan = tenant.plan or "STARTER"
    max_flows = PLAN_LIMITS.get(plan, {}).get("max_fms_flows")
    current = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).count()
    if max_flows is not None and current >= max_flows:
        return _redir("/setup/flows?err=Flow+limit+reached+for+your+plan")

    try:
        stages_data = _jf.loads(stages_json_raw)
    except Exception:
        stages_data = []

    try:
        ticket_form_fields_data = _jf.loads(ticket_form_fields_json_raw)
    except Exception:
        ticket_form_fields_data = []

    flow = FMSFlow(
        id=new_id(),
        tenant_id=user.tenant_id,
        name=name,
        description=description or None,
        color=color,
        is_active=is_active,
        created_by_id=user.id,
        ticket_form_fields_json=_jf.dumps(_clean_ticket_form_fields(ticket_form_fields_data)),
    )
    db.add(flow)
    db.flush()

    for i, s in enumerate(stages_data):
        sname = (s.get("name") or "").strip()
        if not sname:
            continue
        tat = s.get("target_tat_hours")
        if tat is not None:
            try:
                tat = float(tat)
            except Exception:
                tat = None
        tat_unit = (s.get("target_tat_unit") or "hours").strip().lower()
        if tat_unit not in ("minutes", "hours", "days"):
            tat_unit = "hours"
        cf = s.get("custom_fields", [])
        db.add(FMSStage(
            id=s.get("id") or new_id(),
            flow_id=flow.id,
            tenant_id=user.tenant_id,
            name=sname,
            order=i,
            color=(s.get("color") or "#3b82f6").strip(),
            default_assignee_id=s.get("default_assignee_id") or None,
            target_tat_hours=tat,
            target_tat_unit=tat_unit,
            completion_note_required=bool(s.get("completion_note_required")),
            is_terminal=bool(s.get("is_terminal")),
            custom_fields_json=_jf.dumps(cf),
        ))

    db.commit()
    return _redir("/setup/flows?msg=Flow+created")


@router.post("/setup/flows/{flow_id}/edit")
async def setup_flow_update(
    flow_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    import json as _jf
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id,
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).first()
    if not flow:
        return _redir("/setup/flows?err=Flow+not+found")

    form = await request.form()
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    color = (form.get("color") or "#3b82f6").strip()
    is_active = form.get("is_active") == "1"
    stages_json_raw = (form.get("stages_json") or "[]").strip()
    ticket_form_fields_json_raw = form.get("ticket_form_fields_json")

    if not name:
        return _redir(f"/setup/flows/{flow_id}/edit?err=Flow+name+is+required")

    try:
        stages_data = _jf.loads(stages_json_raw)
    except Exception:
        stages_data = []

    flow.name = name
    flow.description = description or None
    flow.color = color
    flow.is_active = is_active
    flow.updated_at = datetime.utcnow()

    if ticket_form_fields_json_raw is not None:
        try:
            ticket_form_fields_data = _jf.loads(ticket_form_fields_json_raw)
            flow.ticket_form_fields_json = _jf.dumps(_clean_ticket_form_fields(ticket_form_fields_data))
        except Exception:
            pass

    submitted_ids = {s.get("id") for s in stages_data if s.get("id")}
    existing = {s.id: s for s in flow.stages if not s.is_deleted}

    for sid, stage in existing.items():
        if sid not in submitted_ids:
            stage.is_deleted = True

    for i, s in enumerate(stages_data):
        sname = (s.get("name") or "").strip()
        if not sname:
            continue
        tat = s.get("target_tat_hours")
        if tat is not None:
            try:
                tat = float(tat)
            except Exception:
                tat = None
        tat_unit = (s.get("target_tat_unit") or "hours").strip().lower()
        if tat_unit not in ("minutes", "hours", "days"):
            tat_unit = "hours"
        cf = s.get("custom_fields", [])
        sid = s.get("id")
        if sid and sid in existing:
            stage = existing[sid]
            stage.name = sname
            stage.order = i
            stage.default_assignee_id = s.get("default_assignee_id") or None
            stage.target_tat_hours = tat
            stage.target_tat_unit = tat_unit
            stage.completion_note_required = bool(s.get("completion_note_required"))
            stage.color = (s.get("color") or "#3b82f6").strip()
            stage.is_terminal = bool(s.get("is_terminal"))
            stage.custom_fields_json = _jf.dumps(cf)
        else:
            db.add(FMSStage(
                id=new_id(),
                flow_id=flow.id,
                tenant_id=user.tenant_id,
                name=sname,
                order=i,
                color=(s.get("color") or "#3b82f6").strip(),
                default_assignee_id=s.get("default_assignee_id") or None,
                target_tat_hours=tat,
                target_tat_unit=tat_unit,
                completion_note_required=bool(s.get("completion_note_required")),
                is_terminal=bool(s.get("is_terminal")),
                custom_fields_json=_jf.dumps(cf),
            ))

    db.commit()
    return _redir("/setup/flows?msg=Flow+updated")


@router.post("/setup/flows/{flow_id}/ticket-form")
async def setup_flow_save_ticket_form(
    flow_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save custom ticket-creation form fields for a flow."""
    from fastapi.responses import JSONResponse
    from .database import new_id
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id,
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).first()
    if not flow:
        return JSONResponse({"ok": False, "error": "Flow not found"}, status_code=404)

    body = await request.json()
    fields = body.get("fields", [])
    clean = _clean_ticket_form_fields(fields)

    flow.ticket_form_fields_json = _json.dumps(clean)
    from datetime import datetime as _dt
    flow.updated_at = _dt.utcnow()
    db.commit()
    return JSONResponse({"ok": True, "field_count": len(clean)})


@router.post("/setup/flows/{flow_id}/delete")
def setup_flow_delete(
    flow_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id,
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).first()
    if not flow:
        return _redir("/setup/flows?err=Flow+not+found")

    active_tickets = db.query(FMSTicket).filter(
        FMSTicket.flow_id == flow_id,
        FMSTicket.is_deleted == False,
        FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
    ).count()
    if active_tickets > 0:
        return _redir("/setup/flows?err=Cannot+delete+flow+with+active+tickets")

    flow.is_deleted = True
    db.commit()
    return _redir("/setup/flows?msg=Flow+deleted")

