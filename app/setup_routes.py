"""
Phase 2 — Setup Module: Customers, End Products, Custom Lists, Org Chart,
Deployed Config, and Inventory Reference routes.
"""
from __future__ import annotations

import csv, io, json, re

_PHONE_RE = re.compile(r'^[0-9+\-\s()]{7,20}$')
from datetime import datetime, date as _date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from markupsafe import Markup
from sqlalchemy.orm import Session
import os

from .database import (
    get_db, new_id,
    User, Tenant, Branch, Department,
    Customer, EndProduct, Vendor, RawMaterial, UnitOfMeasure,
    CustomReferenceList, CustomReferenceItem,
    FMSFlow, FMSStage, FMSTicket, FMSStageHistory,
    ProductVariant, ProductStock,
    SalesOrder, CRMCallLog, CustomerPriceOverride,
    InventoryPurchaseOrder,
    StockLedgerEntry, InventoryPOItem, PriceListItem, PriceListItemHistory,
    CostEntry, SalesOrderItem, StockReservation,
    Category, SubCategory, ProductSchemaField,
)
from .auth import require_admin, require_admin_or_redirect, get_nav_flags, has_module
from .labels import get_labels
from .constants import BULK_IMPORT_MAX_ROWS
from .bulk_common import check_required_headers
from .sales_catalog_sync import (
    sync_variant_from_end_product, attach_drive_photo, resolve_or_create_category_pair,
)

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
        ("__system_employee__",    "Employees",      User,         "name"),
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
    tenant = db.query(Tenant).get(user.tenant_id)
    return get_nav_flags(db, user, tenant)


# ── Customer CSV columns — shared canonical set, also used by Sales CRM's
# contacts bulk template/import/export (app/sales_contacts.py) so all three
# surfaces (Setup import, Sales CRM bulk import, Sales CRM export) round-trip
# the same columns. ────────────────────────────────────────────────────────
_CUSTOMER_COLS = [
    ("name",                "Mandatory. Customer/client name. Max 200 characters."),
    ("contact_person",      "Optional. Primary contact name at the customer."),
    ("phone",               "Optional. Contact phone number."),
    ("email",               "Optional. Contact email address."),
    ("address",             "Optional. Customer address. Free text."),
    ("notes",               "Optional. Any additional notes. Free text."),
    ("agent_email",         "Optional. Email of the assigned salesman (must have Sales access). Defaults to you."),
    ("employee_name",       "Optional. Alternate to agent_email — name of the assigned salesman, if you don't know their email. Ambiguous if two agents share a name."),
    ("tier",                "Optional. Customer tier: A, B, C, or blank for UNRANKED."),
    ("contact_freq_days",   "Optional. Integer. How often (days) this customer should be contacted. Defaults to 30."),
    ("credit_limit",        "Optional. Numeric credit limit."),
    ("gstin",               "Optional. GST identification number."),
    ("billing_address",     "Optional. Billing address, if different from the general address above."),
    ("shipping_address",    "Optional. Shipping address, if different from the general address above."),
    ("default_payment_terms", "Optional. Default payment terms for Sales Orders, e.g. Net 30."),
]

_CUSTOMER_TIERS = ("A", "B", "C")


def resolve_customer_agent(db: Session, tenant_id: str, agent_email: str, employee_name: str = ""):
    """Find-or-error the Sales-access User for an agent_email (preferred) or
    employee_name (fallback, for uploaders who don't know each agent's email)
    column value. Shared by Setup's customer import and Sales CRM's contacts
    bulk import — both surfaces support the same two lookup keys."""
    agent_email = (agent_email or "").strip()
    employee_name = (employee_name or "").strip()
    if agent_email:
        agent = db.query(User).filter(
            User.tenant_id == tenant_id, User.email == agent_email,
            User.is_active == True, User.is_deleted == False,
        ).first()
        if not agent or not has_module(agent, "SALES"):
            return None, f"agent_email {agent_email} not found or lacks SALES access"
        return agent, None
    if employee_name:
        matches = [u for u in db.query(User).filter(
            User.tenant_id == tenant_id, User.is_active == True, User.is_deleted == False,
            func.lower(User.name) == employee_name.lower(),
        ).all() if has_module(u, "SALES")]
        if not matches:
            return None, f"employee_name {employee_name} not found or lacks SALES access"
        if len(matches) > 1:
            return None, f"employee_name {employee_name} matches {len(matches)} agents — ambiguous, use agent_email instead"
        return matches[0], None
    return None, None

_ENDPRODUCT_COLS = [
    ("category",            "Optional. Catalog category name. Created if it doesn't exist yet. Defaults to 'Uncategorized'."),
    ("sub_category",        "Optional. Catalog sub-category name (under category). Created if it doesn't exist yet. Defaults to 'General'."),
    ("name",                "Mandatory. Product name. Max 200 characters."),
    ("description",         "Optional. Product description. Free text."),
    ("sku_code",            "Optional. Must be unique per tenant if provided. Alphanumeric, no spaces."),
    ("variant_label",       "Optional. Display label for the Catalog variant. Defaults to name."),
    ("unit",                "Optional. Unit of measure abbreviation — must match an existing Setup > Units entry, e.g. pcs, kg, litres, box."),
    ("low_stock_threshold", "Optional. Numeric. Godown dashboard flags stock below this level."),
    ("photo_drive_link",    "Optional. Google Drive share link ('Anyone with the link') to a product photo."),
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


def _read_csv_rows(raw: bytes, filename: str) -> list:
    """Decode + parse an uploaded CSV into a list of row dicts, with shared validation."""
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    if (filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload the CSV template, not an Excel file.")
    content = raw.decode("utf-8-sig", errors="replace").lstrip(chr(65279))
    try:
        rows = list(csv.DictReader(io.StringIO(content)))
    except csv.Error:
        raise HTTPException(400, "Could not parse file — please upload a valid CSV using the provided template.")
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(rows)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    return rows


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


def _customers_filtered_query(db: Session, tenant_id: str, status: str, pending: str, tier: str):
    q = db.query(Customer).filter(
        Customer.tenant_id == tenant_id,
        Customer.is_deleted == False,
    )
    if status == "active":
        q = q.filter(Customer.is_active == True)
    elif status == "inactive":
        q = q.filter(Customer.is_active == False)
    if pending == "1":
        q = q.filter(Customer.approval_status == "PENDING")
    if tier:
        q = q.filter(Customer.customer_tier == tier)
    return q


@router.get("/setup/customers", response_class=HTMLResponse)
def customers_page(
    request: Request,
    page: int = 1,
    status: str = "",
    pending: str = "",
    tier: str = "",
    user: User = Depends(require_admin_or_redirect),
    db: Session = Depends(get_db),
):
    q = _customers_filtered_query(db, user.tenant_id, status, pending, tier).order_by(Customer.name)
    total = q.count()
    customers = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    msg = request.query_params.get("msg", "")
    err = request.query_params.get("err", "")
    return templates.TemplateResponse(request, "setup/customers.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "customers": customers, "total": total,
        "page": page, "page_size": PAGE_SIZE,
        "status_filter": status, "pending_filter": pending, "tier_filter": tier,
        "msg": msg, "err": err,
    })


@router.post("/setup/customers/{cust_id}/approve")
def approve_customer(cust_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == cust_id, Customer.tenant_id == user.tenant_id).first()
    if c:
        c.approval_status = "APPROVED"
        db.commit()
    return _redir("/setup/customers?msg=Customer+approved")


@router.post("/setup/customers/add")
def add_customer(
    name: str = Form(...),
    contact_person: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    agent_email: str = Form(""),
    tier: str = Form(""),
    contact_freq_days: str = Form(""),
    credit_limit: str = Form(""),
    gstin: str = Form(""),
    billing_address: str = Form(""),
    shipping_address: str = Form(""),
    default_payment_terms: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return _redir("/setup/customers?err=Name+is+required")
    p = phone.strip()
    if p and not _PHONE_RE.match(p):
        return _redir("/setup/customers?err=Invalid+phone+number+format")
    tier = tier.strip().upper()
    if tier and tier not in _CUSTOMER_TIERS:
        return _redir("/setup/customers?err=Tier+must+be+A%2C+B%2C+C+or+blank")
    agent, agent_err = resolve_customer_agent(db, user.tenant_id, agent_email.strip())
    if agent_err:
        return _redir(f"/setup/customers?err={agent_err}")
    freq = int(contact_freq_days) if contact_freq_days.strip().isdigit() else 30
    credit = float(credit_limit) if credit_limit.strip() else None
    db.add(Customer(
        tenant_id=user.tenant_id,
        name=name.strip(), contact_person=contact_person.strip() or None,
        phone=p or None, email=email.strip() or None,
        address=address.strip() or None, notes=notes.strip() or None,
        assigned_agent_id=agent.id if agent else user.id,
        customer_tier=tier or "UNRANKED",
        contact_freq_days=freq,
        credit_limit=credit,
        gstin=gstin.strip() or None,
        billing_address=billing_address.strip() or None,
        shipping_address=shipping_address.strip() or None,
        default_payment_terms=default_payment_terms.strip() or None,
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
    agent_email: str = Form(""),
    tier: str = Form(""),
    contact_freq_days: str = Form(""),
    credit_limit: str = Form(""),
    gstin: str = Form(""),
    billing_address: str = Form(""),
    shipping_address: str = Form(""),
    default_payment_terms: str = Form(""),
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
    tier = tier.strip().upper()
    if tier and tier not in _CUSTOMER_TIERS:
        return _redir("/setup/customers?err=Tier+must+be+A%2C+B%2C+C+or+blank")
    if agent_email.strip():
        agent, agent_err = resolve_customer_agent(db, user.tenant_id, agent_email.strip())
        if agent_err:
            return _redir(f"/setup/customers?err={agent_err}")
        c.assigned_agent_id = agent.id
    c.name = name.strip()
    c.contact_person = contact_person.strip() or None
    c.phone = p or None
    c.email = email.strip() or None
    c.address = address.strip() or None
    c.notes = notes.strip() or None
    c.customer_tier = tier or "UNRANKED"
    c.contact_freq_days = int(contact_freq_days) if contact_freq_days.strip().isdigit() else c.contact_freq_days
    c.credit_limit = float(credit_limit) if credit_limit.strip() else None
    c.gstin = gstin.strip() or None
    c.billing_address = billing_address.strip() or None
    c.shipping_address = shipping_address.strip() or None
    c.default_payment_terms = default_payment_terms.strip() or None
    c.is_active = is_active == "1"
    c.approval_status = "APPROVED"
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


@router.post("/setup/customers/bulk-delete")
def bulk_delete_customers(
    customer_ids: list[str] = Form(default=[]),
    select_all_filtered: str = Form(""),
    status: str = Form(""),
    pending: str = Form(""),
    tier: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Hard-delete selected customers — permanent, for clearing test/bad data
    before a clean re-upload. Customers with existing sales activity (orders,
    call logs, price overrides) are skipped and reported rather than deleted,
    to avoid destroying real transaction history.

    select_all_filtered: when set, ignores customer_ids and instead resolves
    every customer matching the current list filters (not just the current
    page) — the "select all N matching" affordance."""
    if select_all_filtered:
        customer_ids = [c.id for c in _customers_filtered_query(db, user.tenant_id, status, pending, tier).all()]
    deleted, skipped = 0, []
    for cid in customer_ids:
        c = db.query(Customer).filter(Customer.id == cid, Customer.tenant_id == user.tenant_id).first()
        if not c:
            continue
        blockers = []
        if db.query(SalesOrder).filter(SalesOrder.customer_id == cid).first():
            blockers.append("sales orders")
        if db.query(CRMCallLog).filter(CRMCallLog.customer_id == cid).first():
            blockers.append("call logs")
        if db.query(CustomerPriceOverride).filter(CustomerPriceOverride.customer_id == cid).first():
            blockers.append("price overrides")
        if blockers:
            skipped.append(f"{c.name} (has {', '.join(blockers)})")
            continue
        db.delete(c)
        deleted += 1
    db.commit()
    err = f"&err={len(skipped)}+skipped+(existing+sales+activity)" if skipped else ""
    return _redir(f"/setup/customers?msg={deleted}+customer(s)+deleted{err}")


@router.get("/setup/customers/template")
def customers_template(user: User = Depends(require_admin)):
    return _csv_template(_CUSTOMER_COLS, "customers_template.csv")


@router.post("/setup/customers/import")
async def import_customers(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reader = _read_csv_rows(await file.read(), file.filename)
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
        tier = (row.get("tier") or "").strip().upper()
        if tier and tier not in _CUSTOMER_TIERS:
            errors.append({"row": i, "error": "tier must be A, B, C or blank", "data": dict(row)})
            continue
        agent, agent_err = resolve_customer_agent(db, user.tenant_id, (row.get("agent_email") or "").strip(), (row.get("employee_name") or "").strip())
        if agent_err:
            errors.append({"row": i, "error": agent_err, "data": dict(row)})
            continue
        freq_raw = (row.get("contact_freq_days") or "").strip()
        try:
            freq = int(freq_raw) if freq_raw else 30
        except ValueError:
            errors.append({"row": i, "error": "contact_freq_days must be a positive integer", "data": dict(row)})
            continue
        credit_raw = (row.get("credit_limit") or "").strip()
        try:
            credit = float(credit_raw) if credit_raw else None
        except ValueError:
            errors.append({"row": i, "error": "credit_limit must be a number", "data": dict(row)})
            continue
        db.add(Customer(
            tenant_id=user.tenant_id,
            name=name,
            contact_person=(row.get("contact_person") or "").strip() or None,
            phone=(row.get("phone") or "").strip() or None,
            email=(row.get("email") or "").strip() or None,
            address=(row.get("address") or "").strip() or None,
            notes=(row.get("notes") or "").strip() or None,
            assigned_agent_id=agent.id if agent else user.id,
            customer_tier=tier or "UNRANKED",
            contact_freq_days=freq,
            credit_limit=credit,
            gstin=(row.get("gstin") or "").strip() or None,
            billing_address=(row.get("billing_address") or "").strip() or None,
            shipping_address=(row.get("shipping_address") or "").strip() or None,
            default_payment_terms=(row.get("default_payment_terms") or "").strip() or None,
            created_by_id=user.id,
        ))
        imported += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no customers were created. {e}")
    if errors:
        r = _exception_report(errors, "customers_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/customers?msg=Imported+{imported}+customer(s)")


def _validate_customer_row(row: dict, tenant_id: str, user: User, db: Session) -> tuple:
    name = (row.get("name") or "").strip()
    if not name or name.startswith("Mandatory") or name.startswith("Optional"):
        return None, None  # instructional filler row from the template — silently skip
    if len(name) > 200:
        return None, "name exceeds 200 characters"
    tier = (row.get("tier") or "").strip().upper()
    if tier and tier not in _CUSTOMER_TIERS:
        return None, "tier must be A, B, C or blank"
    agent, agent_err = resolve_customer_agent(db, tenant_id, (row.get("agent_email") or "").strip())
    if agent_err:
        return None, agent_err
    freq_raw = (row.get("contact_freq_days") or "").strip()
    try:
        freq = int(freq_raw) if freq_raw else 30
    except ValueError:
        return None, "contact_freq_days must be a positive integer"
    credit_raw = (row.get("credit_limit") or "").strip()
    try:
        credit = float(credit_raw) if credit_raw else None
    except ValueError:
        return None, "credit_limit must be a number"
    return {
        "name": name,
        "contact_person": (row.get("contact_person") or "").strip() or None,
        "phone": (row.get("phone") or "").strip() or None,
        "email": (row.get("email") or "").strip() or None,
        "address": (row.get("address") or "").strip() or None,
        "notes": (row.get("notes") or "").strip() or None,
        "agent_email": (row.get("agent_email") or "").strip() or None,
        "tier": tier,
        "contact_freq_days": freq,
        "credit_limit": credit,
        "gstin": (row.get("gstin") or "").strip() or None,
        "billing_address": (row.get("billing_address") or "").strip() or None,
        "shipping_address": (row.get("shipping_address") or "").strip() or None,
        "default_payment_terms": (row.get("default_payment_terms") or "").strip() or None,
    }, None


def _run_customer_validation(rows_in: list, tenant_id: str, user: User, db: Session, start_index: int = 2) -> dict:
    valid_rows, errors = [], []
    for i, row in enumerate(rows_in, start=start_index):
        parsed, error = _validate_customer_row(row, tenant_id, user, db)
        if error:
            errors.append({"row": row.get("_row", i), "error": error, "data": dict(row)})
        elif parsed:
            valid_rows.append(parsed)
    return {
        "total": len(valid_rows) + len(errors),
        "valid": len(valid_rows),
        "errors": errors,
        "rows": valid_rows,
    }


@router.get("/setup/customers/bulk-upload", response_class=HTMLResponse)
def customers_bulk_upload_page(request: Request, user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "setup/customers_bulk_upload.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user), **_nav_ctx(db, user),
        "columns": [c[0] for c in _CUSTOMER_COLS],
    })


@router.post("/setup/customers/bulk-upload/validate")
async def customers_bulk_validate(file: UploadFile = File(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    content = raw.decode("utf-8-sig", errors="replace").lstrip(chr(65279))
    dict_reader = csv.DictReader(io.StringIO(content))
    rows = list(dict_reader)
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(rows)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    fmt_err = check_required_headers(dict_reader.fieldnames, ["name"], [c[0] for c in _CUSTOMER_COLS])
    if fmt_err:
        return JSONResponse({"format_error": fmt_err})
    for i, row in enumerate(rows, start=2):
        row["_row"] = i
    return JSONResponse(_run_customer_validation(rows, user.tenant_id, user, db))


@router.post("/setup/customers/bulk-upload/revalidate")
async def customers_bulk_revalidate(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    body = await request.json()
    rows_in = body.get("rows", [])
    if len(rows_in) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Too many rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    return JSONResponse(_run_customer_validation(rows_in, user.tenant_id, user, db))


@router.post("/setup/customers/bulk-upload/confirm")
async def customers_bulk_confirm(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    body = await request.json()
    rows = body.get("rows", [])
    created = 0
    for r in rows:
        agent, agent_err = resolve_customer_agent(db, user.tenant_id, r.get("agent_email") or "", r.get("employee_name") or "")
        if agent_err:
            continue  # re-validated already, but be defensive against stale client state
        db.add(Customer(
            tenant_id=user.tenant_id,
            name=r["name"],
            contact_person=r.get("contact_person"),
            phone=r.get("phone"),
            email=r.get("email"),
            address=r.get("address"),
            notes=r.get("notes"),
            assigned_agent_id=agent.id if agent else user.id,
            customer_tier=r.get("tier") or "UNRANKED",
            contact_freq_days=r.get("contact_freq_days") or 30,
            credit_limit=r.get("credit_limit"),
            gstin=r.get("gstin"),
            billing_address=r.get("billing_address"),
            shipping_address=r.get("shipping_address"),
            default_payment_terms=r.get("default_payment_terms"),
            created_by_id=user.id,
        ))
        created += 1
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no customers were created. {e}")
    return JSONResponse({"created": created})


# ══════════════════════════════════════════════════════════════════════════════
# END PRODUCTS
# Kept bidirectionally in sync with Sales Catalog's Category -> SubCategory ->
# Product -> ProductVariant hierarchy (matched on sku_code). See
# app/sales_catalog_sync.py: sync_end_product_from_variant() is the Catalog ->
# EndProduct direction; sync_variant_from_end_product() (used below) is the
# reverse — it can create a brand-new Category/SubCategory/Product/Variant
# chain when a category/sub_category is supplied here and no variant exists
# yet for the sku.
# ══════════════════════════════════════════════════════════════════════════════

def _end_product_unit_or_error(db: Session, tenant_id: str, unit_abbr: str):
    if not unit_abbr:
        return None, None
    unit = db.query(UnitOfMeasure).filter(
        UnitOfMeasure.tenant_id == tenant_id,
        UnitOfMeasure.abbreviation == unit_abbr,
        UnitOfMeasure.is_active == True,
    ).first()
    if not unit:
        return None, f"Unit '{unit_abbr}' not found. Add it in Setup → Units first."
    return unit, None


def _sku_piece(s: str, length: int) -> str:
    """First `length` alphanumeric characters of s, uppercased, padded with X if too short."""
    letters = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    return (letters + "X" * length)[:length]


def generate_product_sku(
    db: Session, tenant_id: str,
    category_name: str = None, sub_category_name: str = None,
) -> str:
    """Auto-generate a SKU when the user leaves it blank, so every product
    always has one. Format: CC-SSS-#### — 2 chars category, 3 chars
    sub-category, all caps, then a zero-padded sequence number scoped to that
    category+sub-category prefix, incrementing on collision against both
    EndProduct and ProductVariant (the two tables that share the SKU
    namespace — see sales_catalog_sync.py)."""
    prefix = "-".join([
        _sku_piece(category_name or "Uncategorized", 2),
        _sku_piece(sub_category_name or "General", 3),
    ])
    n = 1
    while True:
        candidate = f"{prefix}-{n:04d}"
        exists = (
            db.query(EndProduct).filter(
                EndProduct.tenant_id == tenant_id, EndProduct.sku_code == candidate,
                EndProduct.is_deleted == False,
            ).first()
            or db.query(ProductVariant).filter(
                ProductVariant.tenant_id == tenant_id, ProductVariant.sku_code == candidate,
                ProductVariant.is_deleted == False,
            ).first()
        )
        if not exists:
            return candidate
        n += 1


# Backwards-compatible alias — old name took an unused `name` positional arg
# (item-name was dropped from the generated format; kept as a no-op param
# so existing call sites don't need updating).
def _generate_end_product_sku(db: Session, tenant_id: str, name: str = "",
                               category_name: str = None, sub_category_name: str = None) -> str:
    return generate_product_sku(db, tenant_id, category_name, sub_category_name)


def _end_products_filtered_query(db: Session, tenant_id: str, status: str, pending: str):
    q = db.query(EndProduct).filter(
        EndProduct.tenant_id == tenant_id,
        EndProduct.is_deleted == False,
    )
    if status == "active":
        q = q.filter(EndProduct.is_active == True)
    elif status == "inactive":
        q = q.filter(EndProduct.is_active == False)
    if pending == "1":
        q = q.filter(EndProduct.approval_status == "PENDING")
    return q


@router.get("/setup/end-products", response_class=HTMLResponse)
def end_products_page(
    request: Request,
    page: int = 1,
    status: str = "",
    pending: str = "",
    user: User = Depends(require_admin_or_redirect),
    db: Session = Depends(get_db),
):
    q = _end_products_filtered_query(db, user.tenant_id, status, pending).order_by(EndProduct.name)
    total = q.count()
    products = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    # Setup <-> Sales cross-link (Phase 5 of the UX redesign): EndProduct and
    # ProductVariant already sync bidirectionally on sku_code (sales_catalog_sync.py)
    # — read that same key here, don't recompute a new matching rule.
    skus = [p.sku_code for p in products if p.sku_code]
    variants_by_sku = {
        v.sku_code: v for v in db.query(ProductVariant).filter(
            ProductVariant.tenant_id == user.tenant_id,
            ProductVariant.sku_code.in_(skus),
            ProductVariant.is_deleted == False,
        ).all()
    } if skus else {}
    for p in products:
        p.category_name = p.category.name if p.category else ""
        p.sub_category_name = p.sub_category.name if p.sub_category else ""
        matching_variant = variants_by_sku.get(p.sku_code) if p.sku_code else None
        p.catalog_product_id = matching_variant.product_id if matching_variant else None
    # Add-Product form mirrors Catalog's New Product modal exactly (same
    # dropdown-driven category/unit hierarchy) — see sales_catalog.py's
    # catalog_create, which this form now posts to directly.
    categories = db.query(Category).filter(
        Category.tenant_id == user.tenant_id, Category.is_active == True, Category.is_deleted == False,
    ).order_by(Category.name).all()
    subcategories = db.query(SubCategory).filter(
        SubCategory.tenant_id == user.tenant_id, SubCategory.is_active == True, SubCategory.is_deleted == False,
    ).order_by(SubCategory.name).all()
    units = db.query(UnitOfMeasure).filter(
        UnitOfMeasure.tenant_id == user.tenant_id, UnitOfMeasure.is_active == True, UnitOfMeasure.is_deleted == False,
    ).order_by(UnitOfMeasure.name).all()
    schema_fields = db.query(ProductSchemaField).filter(
        ProductSchemaField.tenant_id == user.tenant_id, ProductSchemaField.is_active == True,
    ).order_by(ProductSchemaField.sort_order).all()

    return templates.TemplateResponse(request, "setup/end_products.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "products": products, "total": total,
        "page": page, "page_size": PAGE_SIZE,
        "status_filter": status, "pending_filter": pending,
        "categories": categories, "subcategories": subcategories,
        "units": units, "schema_fields": schema_fields,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


@router.post("/setup/end-products/add")
async def add_end_product(
    name: str = Form(...),
    sku_code: str = Form(""),
    unit: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    sub_category: str = Form(""),
    variant_label: str = Form(""),
    low_stock_threshold: str = Form(""),
    photo_drive_link: str = Form(""),
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
    else:
        sku = _generate_end_product_sku(db, user.tenant_id, name.strip(), category.strip(), sub_category.strip())
    unit_abbr = unit.strip()
    _, unit_err = _end_product_unit_or_error(db, user.tenant_id, unit_abbr)
    if unit_err:
        return _redir(f"/setup/end-products?err={unit_err}")
    end_product = EndProduct(
        tenant_id=user.tenant_id, name=name.strip(), sku_code=sku,
        unit=unit_abbr or None, description=description.strip() or None,
        created_by_id=user.id,
    )
    db.add(end_product)
    db.flush()
    if category.strip() or sub_category.strip():
        end_product.category_id, end_product.sub_category_id = _resolve_end_product_category(
            db, user.tenant_id, category.strip(), sub_category.strip(),
        )
    low_stock = float(low_stock_threshold) if low_stock_threshold.strip() else None
    variant = sync_variant_from_end_product(
        db, end_product, variant_label=variant_label, low_stock_threshold=low_stock,
    )
    photo_err = None
    if variant and photo_drive_link.strip():
        photo_err = await attach_drive_photo(variant, photo_drive_link.strip())
    db.commit()
    if photo_err:
        return _redir(f"/setup/end-products?msg=Product+added&err=Photo+not+attached:+{photo_err}")
    return _redir("/setup/end-products?msg=Product+added")


@router.post("/setup/end-products/{prod_id}/edit")
async def edit_end_product(
    prod_id: str,
    name: str = Form(...),
    sku_code: str = Form(""),
    unit: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    sub_category: str = Form(""),
    variant_label: str = Form(""),
    low_stock_threshold: str = Form(""),
    photo_drive_link: str = Form(""),
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
    elif not sku:
        sku = p.sku_code or _generate_end_product_sku(db, user.tenant_id, name.strip(), category.strip(), sub_category.strip())
    unit_abbr = unit.strip()
    _, unit_err = _end_product_unit_or_error(db, user.tenant_id, unit_abbr)
    if unit_err:
        return _redir(f"/setup/end-products?err={unit_err}")
    p.name = name.strip()
    p.sku_code = sku
    p.unit = unit_abbr or None
    p.description = description.strip() or None
    p.is_active = is_active == "1"
    p.approval_status = "APPROVED"
    p.updated_at = datetime.utcnow()
    if category.strip() or sub_category.strip():
        p.category_id, p.sub_category_id = _resolve_end_product_category(
            db, user.tenant_id, category.strip(), sub_category.strip(),
        )
    low_stock = float(low_stock_threshold) if low_stock_threshold.strip() else None
    variant = sync_variant_from_end_product(
        db, p, variant_label=variant_label, low_stock_threshold=low_stock,
    )
    photo_err = None
    if variant and photo_drive_link.strip():
        photo_err = await attach_drive_photo(variant, photo_drive_link.strip())
    db.commit()
    if photo_err:
        return _redir(f"/setup/end-products?msg=Product+updated&err=Photo+not+attached:+{photo_err}")
    return _redir("/setup/end-products?msg=Product+updated")


def _resolve_end_product_category(db: Session, tenant_id: str, category_name: str, sub_category_name: str):
    """Find-or-create Category/SubCategory for a manual End Product edit,
    returning (category_id, sub_category_id)."""
    sub = resolve_or_create_category_pair(db, tenant_id, category_name, sub_category_name)
    return sub.category_id, sub.id


@router.post("/setup/end-products/{prod_id}/approve")
def approve_end_product(prod_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.query(EndProduct).filter(EndProduct.id == prod_id, EndProduct.tenant_id == user.tenant_id).first()
    if p:
        p.approval_status = "APPROVED"
        db.commit()
    return _redir("/setup/end-products?msg=Product+approved")

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


def _variant_transaction_blockers(db: Session, variant_id: str) -> list:
    """Real transaction/history tables that should block a hard-delete of a
    ProductVariant. ProductStock is excluded — it's just the current-balance
    row, created alongside the variant, and safe to delete with it."""
    checks = [
        (StockLedgerEntry, "stock ledger entries"),
        (InventoryPOItem, "purchase order line items"),
        (PriceListItem, "price list entries"),
        (PriceListItemHistory, "price history"),
        (CostEntry, "cost entries"),
        (SalesOrderItem, "sales order line items"),
        (StockReservation, "stock reservations"),
        (CustomerPriceOverride, "customer price overrides"),
    ]
    blockers = []
    for model, label in checks:
        if db.query(model).filter(model.variant_id == variant_id).first():
            blockers.append(label)
    return blockers


@router.post("/setup/end-products/bulk-delete")
def bulk_delete_end_products(
    prod_ids: list[str] = Form(default=[]),
    select_all_filtered: str = Form(""),
    status: str = Form(""),
    pending: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Hard-delete selected End Products — permanent, for clearing test/bad
    data before a clean re-upload. Also cascades to the paired Catalog
    ProductVariant (and its stock row) so the SKU is fully freed up. If that
    variant has real transaction history (orders, purchases, price history),
    the whole row is skipped and reported rather than destroying that history.

    select_all_filtered: resolves every product matching the current list
    filters (not just the current page) — the "select all N matching" affordance."""
    if select_all_filtered:
        prod_ids = [p.id for p in _end_products_filtered_query(db, user.tenant_id, status, pending).all()]
    deleted, skipped = 0, []
    for pid in prod_ids:
        p = db.query(EndProduct).filter(EndProduct.id == pid, EndProduct.tenant_id == user.tenant_id).first()
        if not p:
            continue
        variant = None
        if p.sku_code:
            variant = db.query(ProductVariant).filter(
                ProductVariant.tenant_id == user.tenant_id,
                ProductVariant.sku_code == p.sku_code,
                ProductVariant.is_deleted == False,
            ).first()
        if variant:
            blockers = _variant_transaction_blockers(db, variant.id)
            if blockers:
                skipped.append(f"{p.name} (has {', '.join(blockers)})")
                continue
            db.query(ProductStock).filter(ProductStock.variant_id == variant.id).delete()
            db.delete(variant)
        db.delete(p)
        deleted += 1
    db.commit()
    err = f"&err={len(skipped)}+skipped+(existing+transaction+history)" if skipped else ""
    return _redir(f"/setup/end-products?msg={deleted}+product(s)+deleted{err}")


@router.get("/setup/end-products/template")
def end_products_template(user: User = Depends(require_admin)):
    return _csv_template(_ENDPRODUCT_COLS, "end_products_template.csv")


@router.post("/setup/end-products/import")
async def import_end_products(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reader = _read_csv_rows(await file.read(), file.filename)
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
        else:
            sku = _generate_end_product_sku(
                db, user.tenant_id, name,
                (row.get("category") or "").strip(), (row.get("sub_category") or "").strip(),
            )
        unit_abbr = (row.get("unit") or "").strip()
        _, unit_err = _end_product_unit_or_error(db, user.tenant_id, unit_abbr)
        if unit_err:
            errors.append({"row": i, "error": unit_err, "data": dict(row)})
            continue
        end_product = EndProduct(
            tenant_id=user.tenant_id, name=name, sku_code=sku,
            unit=unit_abbr or None,
            description=(row.get("description") or "").strip() or None,
            created_by_id=user.id,
        )
        db.add(end_product)
        db.flush()
        category = (row.get("category") or "").strip()
        sub_category = (row.get("sub_category") or "").strip()
        if category or sub_category:
            end_product.category_id, end_product.sub_category_id = _resolve_end_product_category(
                db, user.tenant_id, category, sub_category,
            )
        low_stock_raw = (row.get("low_stock_threshold") or "").strip()
        variant = sync_variant_from_end_product(
            db, end_product,
            variant_label=row.get("variant_label"),
            low_stock_threshold=float(low_stock_raw) if low_stock_raw else None,
        )
        drive_link = (row.get("photo_drive_link") or "").strip()
        if variant and drive_link:
            photo_err = await attach_drive_photo(variant, drive_link)
            if photo_err:
                errors.append({"row": i, "error": f"Photo not attached: {photo_err}", "data": dict(row)})
        imported += 1
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no products were created. {e}")
    if errors:
        r = _exception_report(errors, "end_products_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/end-products?msg=Imported+{imported}+product(s)")


def _validate_end_product_row(row: dict, tenant_id: str, db: Session) -> tuple:
    name = (row.get("name") or "").strip()
    if not name or name.startswith("Mandatory") or name.startswith("Optional"):
        return None, None  # instructional filler row from the template — silently skip
    if len(name) > 200:
        return None, "name exceeds 200 characters"
    sku = (row.get("sku_code") or "").strip() or None
    if sku:
        exists = db.query(EndProduct).filter(
            EndProduct.tenant_id == tenant_id,
            EndProduct.sku_code == sku, EndProduct.is_deleted == False,
        ).first()
        if exists:
            return None, f"SKU {sku} already exists"
    unit_abbr = (row.get("unit") or "").strip()
    _, unit_err = _end_product_unit_or_error(db, tenant_id, unit_abbr)
    if unit_err:
        return None, unit_err
    low_stock_raw = (row.get("low_stock_threshold") or "").strip()
    if low_stock_raw:
        try:
            float(low_stock_raw)
        except ValueError:
            return None, "low_stock_threshold must be a number"
    return {
        "name": name,
        "sku_code": sku,
        "unit": unit_abbr or None,
        "description": (row.get("description") or "").strip() or None,
        "category": (row.get("category") or "").strip() or None,
        "sub_category": (row.get("sub_category") or "").strip() or None,
        "variant_label": (row.get("variant_label") or "").strip() or None,
        "low_stock_threshold": low_stock_raw or None,
        "photo_drive_link": (row.get("photo_drive_link") or "").strip() or None,
    }, None


def _run_end_product_validation(rows_in: list, tenant_id: str, db: Session, start_index: int = 2) -> dict:
    valid_rows, errors = [], []
    seen_skus_in_file = set()
    for i, row in enumerate(rows_in, start=start_index):
        parsed, error = _validate_end_product_row(row, tenant_id, db)
        if not error and parsed and parsed["sku_code"]:
            if parsed["sku_code"] in seen_skus_in_file:
                error = f"SKU {parsed['sku_code']} duplicated within file"
                parsed = None
            else:
                seen_skus_in_file.add(parsed["sku_code"])
        if error:
            errors.append({"row": row.get("_row", i), "error": error, "data": dict(row)})
        elif parsed:
            valid_rows.append(parsed)
    return {
        "total": len(valid_rows) + len(errors),
        "valid": len(valid_rows),
        "errors": errors,
        "rows": valid_rows,
    }


@router.get("/setup/end-products/bulk-upload", response_class=HTMLResponse)
def end_products_bulk_upload_page(request: Request, user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "setup/end_products_bulk_upload.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user), **_nav_ctx(db, user),
        "columns": [c[0] for c in _ENDPRODUCT_COLS],
    })


@router.post("/setup/end-products/bulk-upload/validate")
async def end_products_bulk_validate(file: UploadFile = File(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    content = raw.decode("utf-8-sig", errors="replace").lstrip(chr(65279))
    dict_reader = csv.DictReader(io.StringIO(content))
    rows = list(dict_reader)
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(rows)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    fmt_err = check_required_headers(dict_reader.fieldnames, ["name"], [c[0] for c in _ENDPRODUCT_COLS])
    if fmt_err:
        return JSONResponse({"format_error": fmt_err})
    for i, row in enumerate(rows, start=2):
        row["_row"] = i
    return JSONResponse(_run_end_product_validation(rows, user.tenant_id, db))


@router.post("/setup/end-products/bulk-upload/revalidate")
async def end_products_bulk_revalidate(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    body = await request.json()
    rows_in = body.get("rows", [])
    if len(rows_in) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Too many rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    return JSONResponse(_run_end_product_validation(rows_in, user.tenant_id, db))


@router.post("/setup/end-products/bulk-upload/confirm")
async def end_products_bulk_confirm(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    body = await request.json()
    rows = body.get("rows", [])
    created = 0
    warnings = []
    try:
        for r in rows:
            sku = r.get("sku_code")
            if sku:
                exists = db.query(EndProduct).filter(
                    EndProduct.tenant_id == user.tenant_id,
                    EndProduct.sku_code == sku, EndProduct.is_deleted == False,
                ).first()
                if exists:
                    warnings.append(f"SKU {sku} already exists — skipped")
                    continue
            else:
                sku = _generate_end_product_sku(
                    db, user.tenant_id, r["name"],
                    r.get("category") or "", r.get("sub_category") or "",
                )
            end_product = EndProduct(
                tenant_id=user.tenant_id, name=r["name"], sku_code=sku,
                unit=r.get("unit"),
                description=r.get("description"),
                created_by_id=user.id,
            )
            db.add(end_product)
            db.flush()
            category = r.get("category") or ""
            sub_category = r.get("sub_category") or ""
            if category or sub_category:
                end_product.category_id, end_product.sub_category_id = _resolve_end_product_category(
                    db, user.tenant_id, category, sub_category,
                )
            low_stock_raw = r.get("low_stock_threshold")
            variant = sync_variant_from_end_product(
                db, end_product,
                variant_label=r.get("variant_label"),
                low_stock_threshold=float(low_stock_raw) if low_stock_raw else None,
            )
            drive_link = r.get("photo_drive_link")
            if variant and drive_link:
                photo_err = await attach_drive_photo(variant, drive_link)
                if photo_err:
                    warnings.append(f"{r['name']}: photo not attached — {photo_err}")
            created += 1
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no products were created. {e}")
    return JSONResponse({"created": created, "warnings": warnings})


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOM REFERENCE LISTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/custom-lists", response_class=HTMLResponse)
def custom_lists_page(
    request: Request,
    user: User = Depends(require_admin_or_redirect),
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
def howto_page(request: Request, user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
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


@router.post("/setup/custom-lists/items/{item_id}/approve")
def approve_list_item(
    item_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(CustomReferenceItem).filter(
        CustomReferenceItem.id == item_id,
        CustomReferenceItem.tenant_id == user.tenant_id,
        CustomReferenceItem.is_deleted == False,
    ).first()
    if item:
        item.approval_status = "APPROVED"
        db.commit()
    return _redir("/setup/custom-lists?msg=Item+approved")


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
    reader = _read_csv_rows(await file.read(), file.filename)
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

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no items were created. {e}")
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
    user: User = Depends(require_admin_or_redirect),
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

def _vendors_filtered_query(db: Session, tenant_id: str, status: str, pending: str):
    q = db.query(Vendor).filter(Vendor.tenant_id == tenant_id, Vendor.is_deleted == False)
    if status == "active":
        q = q.filter(Vendor.is_active == True)
    elif status == "inactive":
        q = q.filter(Vendor.is_active == False)
    if pending == "1":
        q = q.filter(Vendor.approval_status == "PENDING")
    return q


@router.get("/setup/vendors", response_class=HTMLResponse)
def vendors_page(request: Request, page: int = 1, status: str = "", pending: str = "",
                 user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
    q = _vendors_filtered_query(db, user.tenant_id, status, pending).order_by(Vendor.name)
    total = q.count()
    vendors = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return templates.TemplateResponse(request, "setup/vendors.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "vendors": vendors, "total": total, "page": page, "page_size": PAGE_SIZE,
        "status_filter": status, "pending_filter": pending,
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
    v.approval_status = "APPROVED"
    db.commit()
    return _redir("/setup/vendors?msg=Vendor+updated")

@router.post("/setup/vendors/{vendor_id}/approve")
def approve_vendor(vendor_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.tenant_id == user.tenant_id).first()
    if v:
        v.approval_status = "APPROVED"
        db.commit()
    return _redir("/setup/vendors?msg=Vendor+approved")

@router.post("/setup/vendors/{vendor_id}/delete")
def delete_vendor(vendor_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.tenant_id == user.tenant_id).first()
    if v:
        v.is_deleted = True; db.commit()
    return _redir("/setup/vendors?msg=Vendor+deleted")

@router.post("/setup/vendors/bulk-delete")
def bulk_delete_vendors(
    vendor_ids: list[str] = Form(default=[]),
    select_all_filtered: str = Form(""),
    status: str = Form(""),
    pending: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Hard-delete selected vendors — permanent. Vendors with existing
    purchase orders are skipped and reported, to avoid destroying real
    purchase history.

    select_all_filtered: resolves every vendor matching the current list
    filters (not just the current page) — the "select all N matching" affordance."""
    if select_all_filtered:
        vendor_ids = [v.id for v in _vendors_filtered_query(db, user.tenant_id, status, pending).all()]
    deleted, skipped = 0, []
    for vid in vendor_ids:
        v = db.query(Vendor).filter(Vendor.id == vid, Vendor.tenant_id == user.tenant_id).first()
        if not v:
            continue
        if db.query(InventoryPurchaseOrder).filter(InventoryPurchaseOrder.vendor_id == vid).first():
            skipped.append(v.name)
            continue
        db.delete(v)
        deleted += 1
    db.commit()
    err = f"&err={len(skipped)}+skipped+(existing+purchase+orders)" if skipped else ""
    return _redir(f"/setup/vendors?msg={deleted}+vendor(s)+deleted{err}")

@router.get("/setup/vendors/template")
def vendors_template(user: User = Depends(require_admin)):
    return _csv_template(_VENDOR_COLS, "vendors_template.csv")

@router.post("/setup/vendors/import")
async def import_vendors(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reader = _read_csv_rows(await file.read(), file.filename)
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
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no vendors were created. {e}")
    if errors:
        r = _exception_report(errors, "vendors_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/vendors?msg=Imported+{imported}+vendor(s)")


def _validate_vendor_row(row: dict, start_row: int) -> tuple:
    name = (row.get("name") or "").strip()
    if not name or name.startswith("Mandatory") or name.startswith("Optional"):
        return None, None  # instructional filler row from the template — silently skip
    if len(name) > 200:
        return None, "name exceeds 200 characters"
    phone = (row.get("phone") or "").strip()
    if phone and not _PHONE_RE.match(phone):
        return None, "invalid phone number format"
    return {
        "name": name,
        "contact_person": (row.get("contact_person") or "").strip() or None,
        "phone": phone or None,
        "email": (row.get("email") or "").strip() or None,
        "address": (row.get("address") or "").strip() or None,
        "parts_supplied": (row.get("parts_supplied") or "").strip() or None,
        "notes": (row.get("notes") or "").strip() or None,
    }, None


def _run_vendor_validation(rows_in: list, start_index: int = 2) -> dict:
    valid_rows, errors = [], []
    for i, row in enumerate(rows_in, start=start_index):
        parsed, error = _validate_vendor_row(row, i)
        if error:
            errors.append({"row": row.get("_row", i), "error": error, "data": dict(row)})
        elif parsed:
            valid_rows.append(parsed)
    return {
        "total": len(valid_rows) + len(errors),
        "valid": len(valid_rows),
        "errors": errors,
        "rows": valid_rows,
    }


@router.get("/setup/vendors/bulk-upload", response_class=HTMLResponse)
def vendors_bulk_upload_page(request: Request, user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "setup/vendors_bulk_upload.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user), **_nav_ctx(db, user),
        "columns": [c[0] for c in _VENDOR_COLS],
    })


@router.post("/setup/vendors/bulk-upload/validate")
async def vendors_bulk_validate(file: UploadFile = File(...), user: User = Depends(require_admin)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    content = raw.decode("utf-8-sig", errors="replace").lstrip(chr(65279))
    dict_reader = csv.DictReader(io.StringIO(content))
    rows = list(dict_reader)
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(rows)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    fmt_err = check_required_headers(dict_reader.fieldnames, ["name"], [c[0] for c in _VENDOR_COLS])
    if fmt_err:
        return JSONResponse({"format_error": fmt_err})
    for i, row in enumerate(rows, start=2):
        row["_row"] = i
    return JSONResponse(_run_vendor_validation(rows))


@router.post("/setup/vendors/bulk-upload/revalidate")
async def vendors_bulk_revalidate(request: Request, user: User = Depends(require_admin)):
    body = await request.json()
    rows_in = body.get("rows", [])
    if len(rows_in) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Too many rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    return JSONResponse(_run_vendor_validation(rows_in))


@router.post("/setup/vendors/bulk-upload/confirm")
async def vendors_bulk_confirm(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    body = await request.json()
    rows = body.get("rows", [])
    created = 0
    for r in rows:
        db.add(Vendor(
            tenant_id=user.tenant_id, name=r["name"], contact_person=r.get("contact_person"),
            phone=r.get("phone"), email=r.get("email"), address=r.get("address"),
            parts_supplied=r.get("parts_supplied"), notes=r.get("notes"), created_by_id=user.id,
        ))
        created += 1
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no vendors were created. {e}")
    return JSONResponse({"created": created})


# ══════════════════════════════════════════════════════════════════════════════
# RAW MATERIALS
# ══════════════════════════════════════════════════════════════════════════════

def _raw_materials_filtered_query(db: Session, tenant_id: str, status: str, pending: str):
    q = db.query(RawMaterial).filter(RawMaterial.tenant_id == tenant_id, RawMaterial.is_deleted == False)
    if status == "active":
        q = q.filter(RawMaterial.is_active == True)
    elif status == "inactive":
        q = q.filter(RawMaterial.is_active == False)
    if pending == "1":
        q = q.filter(RawMaterial.approval_status == "PENDING")
    return q


@router.get("/setup/raw-materials", response_class=HTMLResponse)
def raw_materials_page(request: Request, page: int = 1, status: str = "", pending: str = "",
                       user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
    q = _raw_materials_filtered_query(db, user.tenant_id, status, pending).order_by(RawMaterial.name)
    total = q.count()
    items = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return templates.TemplateResponse(request, "setup/raw_materials.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user),
        **_nav_ctx(db, user),
        "items": items, "total": total, "page": page, "page_size": PAGE_SIZE,
        "status_filter": status, "pending_filter": pending,
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
    m.approval_status = "APPROVED"
    db.commit()
    return _redir("/setup/raw-materials?msg=Raw+material+updated")

@router.post("/setup/raw-materials/{item_id}/approve")
def approve_raw_material(item_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    m = db.query(RawMaterial).filter(RawMaterial.id == item_id, RawMaterial.tenant_id == user.tenant_id).first()
    if m:
        m.approval_status = "APPROVED"
        db.commit()
    return _redir("/setup/raw-materials?msg=Raw+material+approved")

@router.post("/setup/raw-materials/{item_id}/delete")
def delete_raw_material(item_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    m = db.query(RawMaterial).filter(RawMaterial.id == item_id, RawMaterial.tenant_id == user.tenant_id).first()
    if m:
        m.is_deleted = True; db.commit()
    return _redir("/setup/raw-materials?msg=Raw+material+deleted")

@router.post("/setup/raw-materials/bulk-delete")
def bulk_delete_raw_materials(
    item_ids: list[str] = Form(default=[]),
    select_all_filtered: str = Form(""),
    status: str = Form(""),
    pending: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Hard-delete selected raw materials — permanent. No other table
    references raw_materials, so this is always safe.

    select_all_filtered: resolves every item matching the current list
    filters (not just the current page) — the "select all N matching" affordance."""
    if select_all_filtered:
        item_ids = [m.id for m in _raw_materials_filtered_query(db, user.tenant_id, status, pending).all()]
    deleted = 0
    for iid in item_ids:
        m = db.query(RawMaterial).filter(RawMaterial.id == iid, RawMaterial.tenant_id == user.tenant_id).first()
        if not m:
            continue
        db.delete(m)
        deleted += 1
    db.commit()
    return _redir(f"/setup/raw-materials?msg={deleted}+raw+material(s)+deleted")

@router.get("/setup/raw-materials/template")
def raw_materials_template(user: User = Depends(require_admin)):
    return _csv_template(_RAW_MATERIAL_COLS, "raw_materials_template.csv")

@router.post("/setup/raw-materials/import")
async def import_raw_materials(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reader = _read_csv_rows(await file.read(), file.filename)
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
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no raw materials were created. {e}")
    if errors:
        r = _exception_report(errors, "raw_materials_exceptions.csv")
        r.headers["X-Imported"] = str(imported)
        return r
    return _redir(f"/setup/raw-materials?msg=Imported+{imported}+item(s)")


def _validate_raw_material_row(row: dict) -> tuple:
    name = (row.get("name") or "").strip()
    if not name or name.startswith("Mandatory") or name.startswith("Optional"):
        return None, None  # instructional filler row from the template — silently skip
    if len(name) > 200:
        return None, "name exceeds 200 characters"
    return {
        "name": name,
        "unit": (row.get("unit") or "").strip() or None,
        "description": (row.get("description") or "").strip() or None,
        "major_supplier": (row.get("major_supplier") or "").strip() or None,
        "notes": (row.get("notes") or "").strip() or None,
    }, None


def _run_raw_material_validation(rows_in: list, start_index: int = 2) -> dict:
    valid_rows, errors = [], []
    for i, row in enumerate(rows_in, start=start_index):
        parsed, error = _validate_raw_material_row(row)
        if error:
            errors.append({"row": row.get("_row", i), "error": error, "data": dict(row)})
        elif parsed:
            valid_rows.append(parsed)
    return {
        "total": len(valid_rows) + len(errors),
        "valid": len(valid_rows),
        "errors": errors,
        "rows": valid_rows,
    }


@router.get("/setup/raw-materials/bulk-upload", response_class=HTMLResponse)
def raw_materials_bulk_upload_page(request: Request, user: User = Depends(require_admin_or_redirect), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "setup/raw_materials_bulk_upload.html", {
        "user": user, "unread": _unread(db, user), "L": _L(db, user), **_nav_ctx(db, user),
        "columns": [c[0] for c in _RAW_MATERIAL_COLS],
    })


@router.post("/setup/raw-materials/bulk-upload/validate")
async def raw_materials_bulk_validate(file: UploadFile = File(...), user: User = Depends(require_admin)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    content = raw.decode("utf-8-sig", errors="replace").lstrip(chr(65279))
    dict_reader = csv.DictReader(io.StringIO(content))
    rows = list(dict_reader)
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(rows)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    fmt_err = check_required_headers(dict_reader.fieldnames, ["name"], [c[0] for c in _RAW_MATERIAL_COLS])
    if fmt_err:
        return JSONResponse({"format_error": fmt_err})
    for i, row in enumerate(rows, start=2):
        row["_row"] = i
    return JSONResponse(_run_raw_material_validation(rows))


@router.post("/setup/raw-materials/bulk-upload/revalidate")
async def raw_materials_bulk_revalidate(request: Request, user: User = Depends(require_admin)):
    body = await request.json()
    rows_in = body.get("rows", [])
    if len(rows_in) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Too many rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    return JSONResponse(_run_raw_material_validation(rows_in))


@router.post("/setup/raw-materials/bulk-upload/confirm")
async def raw_materials_bulk_confirm(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    body = await request.json()
    rows = body.get("rows", [])
    created = 0
    for r in rows:
        db.add(RawMaterial(
            tenant_id=user.tenant_id, name=r["name"], unit=r.get("unit"),
            description=r.get("description"), major_supplier=r.get("major_supplier"),
            notes=r.get("notes"), created_by_id=user.id,
        ))
        created += 1
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no raw materials were created. {e}")
    return JSONResponse({"created": created})


# ══════════════════════════════════════════════════════════════════════════════
# ORG CHART
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/setup/org-chart", response_class=HTMLResponse)
def org_chart_page(
    request: Request,
    user: User = Depends(require_admin_or_redirect),
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
def setup_employees_redirect(user: User = Depends(require_admin_or_redirect)):
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


_CLOSING_RULE_OPS = {"==", "!=", "<", "<=", ">", ">="}


def _parse_closing_rule(raw: str | None) -> dict | None:
    """Validate the closing-rule payload from the flow builder.
    Rule shape: {col_id, op, value} — col_id references a ticket-form field
    or a stage custom column by id; value must be numeric."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    col_id = (data.get("col_id") or "").strip()
    op = (data.get("op") or "").strip()
    value = data.get("value")
    if not col_id or op not in _CLOSING_RULE_OPS:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return {"col_id": col_id, "op": op, "value": value}


@router.get("/setup/flows", response_class=HTMLResponse)
def setup_flows_list(
    request: Request,
    status: str = "active",
    user: User = Depends(require_admin_or_redirect),
    db: Session = Depends(get_db),
):
    from .constants import has_feature, PLAN_LIMITS
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "FMS", db):
        return _redir("/setup?err=FMS+not+enabled")

    if status not in ("active", "deleted", "all"):
        status = "active"

    q = db.query(FMSFlow).filter(FMSFlow.tenant_id == user.tenant_id)
    if status == "active":
        q = q.filter(FMSFlow.is_deleted == False)
    elif status == "deleted":
        q = q.filter(FMSFlow.is_deleted == True)
    flows = q.order_by(FMSFlow.created_at).all()

    # Plan limit is based on active (non-deleted) flow count only.
    active_count = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == False,
    ).count()
    plan = tenant.plan or "STARTER"
    max_flows = PLAN_LIMITS.get(plan, {}).get("max_fms_flows")
    at_limit = max_flows is not None and active_count >= max_flows

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
        "status_filter": status,
    })


@router.get("/setup/flows/new", response_class=HTMLResponse)
def setup_flow_new(
    request: Request,
    user: User = Depends(require_admin_or_redirect),
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
        "closing_rule_json": "null",
    })


@router.get("/setup/flows/{flow_id}/edit", response_class=HTMLResponse)
def setup_flow_edit_get(
    flow_id: str,
    request: Request,
    user: User = Depends(require_admin_or_redirect),
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
            "evidence_required": s.evidence_required,
            "is_terminal": s.is_terminal,
            "custom_fields": cf,
            "split_enabled": s.split_enabled,
            "split_target_field": s.split_target_field or "",
            "split_actual_field": s.split_actual_field or "",
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
        "closing_rule_json": flow.closing_rule_json or "null",
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

    closing_rule = _parse_closing_rule(form.get("closing_rule_json"))

    flow = FMSFlow(
        id=new_id(),
        tenant_id=user.tenant_id,
        name=name,
        description=description or None,
        color=color,
        is_active=is_active,
        created_by_id=user.id,
        ticket_form_fields_json=_jf.dumps(_clean_ticket_form_fields(ticket_form_fields_data)),
        closing_rule_json=_jf.dumps(closing_rule) if closing_rule else None,
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
            evidence_required=bool(s.get("evidence_required")),
            is_terminal=bool(s.get("is_terminal")),
            custom_fields_json=_jf.dumps(cf),
            split_enabled=bool(s.get("split_enabled")),
            split_target_field=(s.get("split_target_field") or None),
            split_actual_field=(s.get("split_actual_field") or None),
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
    closing_rule = _parse_closing_rule(form.get("closing_rule_json"))
    flow.closing_rule_json = _jf.dumps(closing_rule) if closing_rule else None

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
            stage.evidence_required = bool(s.get("evidence_required"))
            stage.color = (s.get("color") or "#3b82f6").strip()
            stage.is_terminal = bool(s.get("is_terminal"))
            stage.custom_fields_json = _jf.dumps(cf)
            stage.split_enabled = bool(s.get("split_enabled"))
            stage.split_target_field = s.get("split_target_field") or None
            stage.split_actual_field = s.get("split_actual_field") or None
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
                evidence_required=bool(s.get("evidence_required")),
                is_terminal=bool(s.get("is_terminal")),
                custom_fields_json=_jf.dumps(cf),
                split_enabled=bool(s.get("split_enabled")),
                split_target_field=(s.get("split_target_field") or None),
                split_actual_field=(s.get("split_actual_field") or None),
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


@router.post("/setup/flows/{flow_id}/restore")
def setup_flow_restore(
    flow_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id,
        FMSFlow.tenant_id == user.tenant_id,
        FMSFlow.is_deleted == True,
    ).first()
    if not flow:
        return _redir("/setup/flows?status=deleted&err=Flow+not+found")

    from .constants import has_feature, PLAN_LIMITS
    tenant = db.query(Tenant).get(user.tenant_id)
    plan = tenant.plan or "STARTER"
    max_flows = PLAN_LIMITS.get(plan, {}).get("max_fms_flows")
    if max_flows is not None:
        active_count = db.query(FMSFlow).filter(
            FMSFlow.tenant_id == user.tenant_id,
            FMSFlow.is_deleted == False,
        ).count()
        if active_count >= max_flows:
            return _redir("/setup/flows?status=deleted&err=Flow+limit+reached+on+your+plan")

    flow.is_deleted = False
    db.commit()
    return _redir("/setup/flows?msg=Flow+restored")

