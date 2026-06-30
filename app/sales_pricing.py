"""
Sales Pricing & Margin — Brief 06.
Price lists, customer-specific overrides, cost entry ledger, margin report,
price trend data (foundation for Brief 07).
"""
import csv
import io
from collections import defaultdict
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import func, or_ as _or
from sqlalchemy.orm import Session
from typing import Optional

from .database import (
    get_db, new_id, User, Customer, Product,
    PriceList, PriceListItem, PriceListItemHistory, CustomerPriceOverride, CostEntry,
    SalesOrder, SalesOrderItem,
)
from .auth import get_current_user, has_module, require_module
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread

router = APIRouter()

COST_TYPES = ("BUY_PRICE", "FREIGHT", "HANDLING", "OTHER")

_require_sales = require_module("SALES", "SALES_MODULE")


def _require_pricing_admin(user: User = Depends(_require_sales)) -> User:
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Admin/Manager only")
    return user


def _ctx(db: Session, user: User, **extra) -> dict:
    ctx = {
        "user": user, "L": _L(db, user), "unread": _unread(db, user),
    }
    ctx.update(_nav_ctx(db, user))
    ctx.update(extra)
    return ctx


def _redir(url: str):
    return RedirectResponse(url, status_code=303)


def get_price_list_or_404(db: Session, list_id: str, tenant_id: str) -> PriceList:
    pl = db.query(PriceList).filter(
        PriceList.id == list_id, PriceList.tenant_id == tenant_id,
        PriceList.is_deleted == False,
    ).first()
    if not pl:
        raise HTTPException(404, "Price list not found")
    return pl


def set_price_list_item(db, price_list_id: str, product_id: str,
                         unit_price: float, tenant_id: str, changed_by_id: str):
    """Set or update a product's price in a list. Writes a history row."""
    existing = db.query(PriceListItem).filter(
        PriceListItem.price_list_id == price_list_id,
        PriceListItem.product_id    == product_id,
    ).first()

    old_price = existing.unit_price if existing else None

    if existing:
        existing.unit_price = unit_price
        existing.updated_at = datetime.utcnow()
    else:
        db.add(PriceListItem(
            price_list_id = price_list_id,
            product_id    = product_id,
            tenant_id     = tenant_id,
            unit_price    = unit_price,
        ))

    price_list = db.query(PriceList).filter(PriceList.id == price_list_id).first()
    db.add(PriceListItemHistory(
        price_list_id            = price_list_id,
        price_list_name_snapshot = price_list.name if price_list else None,
        product_id               = product_id,
        tenant_id                = tenant_id,
        old_price                = old_price,
        new_price                = unit_price,
        changed_by_id            = changed_by_id,
    ))
    db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/pricing", response_class=HTMLResponse)
def pricing_overview(
    request: Request,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    list_count = db.query(func.count(PriceList.id)).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
    ).scalar() or 0

    override_count = db.query(func.count(CustomerPriceOverride.id)).filter(
        CustomerPriceOverride.tenant_id == user.tenant_id,
        CustomerPriceOverride.is_active == True,
    ).scalar() or 0

    all_products = db.query(Product.id).filter(
        Product.tenant_id == user.tenant_id, Product.is_deleted == False,
        Product.is_active == True,
    ).all()
    priced_product_ids = {
        r[0] for r in db.query(PriceListItem.product_id).filter(
            PriceListItem.tenant_id == user.tenant_id, PriceListItem.is_active == True,
        ).all()
    }
    no_price_count = len([p for p in all_products if p[0] not in priced_product_ids])

    cutoff = datetime.utcnow() - timedelta(days=30)
    margin_rows = (
        db.query(
            SalesOrderItem.product_id,
            func.sum(SalesOrderItem.line_total).label("revenue"),
            func.sum(SalesOrderItem.cost_snapshot * SalesOrderItem.qty_ordered).label("cost"),
        )
        .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
        .filter(
            SalesOrder.tenant_id == user.tenant_id,
            SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
            SalesOrder.is_deleted == False,
            SalesOrder.created_at >= cutoff,
            SalesOrderItem.cost_snapshot.isnot(None),
        )
        .group_by(SalesOrderItem.product_id)
        .all()
    )
    margins = []
    for row in margin_rows:
        revenue = row.revenue or 0
        cost = row.cost or 0
        if revenue > 0:
            product = db.query(Product).filter(Product.id == row.product_id).first()
            margins.append({
                "product": product,
                "margin": (revenue - cost) / revenue * 100,
            })
    margins.sort(key=lambda m: m["margin"], reverse=True)
    top_margin = margins[:5]
    bottom_margin = list(reversed(margins[-5:])) if len(margins) > 5 else list(reversed(margins))

    return templates.TemplateResponse(request, "sales/pricing_overview.html", _ctx(
        db, user,
        list_count=list_count, override_count=override_count, no_price_count=no_price_count,
        top_margin=top_margin, bottom_margin=bottom_margin,
    ))


# ══════════════════════════════════════════════════════════════════════════════
# PRICE LISTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/pricing/lists", response_class=HTMLResponse)
def pricing_lists(
    request: Request,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    lists = db.query(PriceList).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
    ).order_by(PriceList.name).all()

    return templates.TemplateResponse(request, "sales/pricing_lists.html", _ctx(
        db, user, lists=lists, is_admin=user.role in ("ADMIN", "MANAGER"),
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/sales/pricing/lists/create")
def pricing_list_create(
    name: str = Form(...),
    description: str = Form(""),
    valid_from: str = Form(""),
    valid_to: str = Form(""),
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return _redir("/sales/pricing/lists?err=Name+is+required")

    vf = date.fromisoformat(valid_from) if valid_from.strip() else None
    vt = date.fromisoformat(valid_to) if valid_to.strip() else None

    is_first = db.query(func.count(PriceList.id)).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
    ).scalar() == 0

    pl = PriceList(
        tenant_id=user.tenant_id, name=name.strip(), description=description.strip() or None,
        valid_from=vf, valid_to=vt, is_default=is_first, created_by_id=user.id,
    )
    db.add(pl)
    db.commit()
    return _redir("/sales/pricing/lists?msg=Price+list+created")


@router.post("/sales/pricing/lists/{list_id}/edit")
def pricing_list_edit(
    list_id: str,
    name: str = Form(...),
    description: str = Form(""),
    valid_from: str = Form(""),
    valid_to: str = Form(""),
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    pl = get_price_list_or_404(db, list_id, user.tenant_id)
    if not name.strip():
        return _redir("/sales/pricing/lists?err=Name+is+required")
    pl.name = name.strip()
    pl.description = description.strip() or None
    pl.valid_from = date.fromisoformat(valid_from) if valid_from.strip() else None
    pl.valid_to = date.fromisoformat(valid_to) if valid_to.strip() else None
    pl.updated_at = datetime.utcnow()
    db.commit()
    return _redir("/sales/pricing/lists?msg=Price+list+updated")


@router.post("/sales/pricing/lists/{list_id}/delete")
def pricing_list_delete(
    list_id: str,
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    pl = get_price_list_or_404(db, list_id, user.tenant_id)
    active_count = db.query(func.count(PriceList.id)).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
        PriceList.is_active == True,
    ).scalar() or 0
    if active_count <= 1 and pl.is_active:
        return _redir("/sales/pricing/lists?err=Cannot+delete+the+only+active+price+list")
    pl.is_deleted = True
    pl.is_active = False
    db.commit()
    return _redir("/sales/pricing/lists?msg=Price+list+deleted")


@router.post("/sales/pricing/lists/{list_id}/set-default")
def pricing_list_set_default(
    list_id: str,
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    get_price_list_or_404(db, list_id, user.tenant_id)
    db.query(PriceList).filter(
        PriceList.tenant_id == user.tenant_id,
        PriceList.is_deleted == False,
    ).update({"is_default": False})
    db.query(PriceList).filter(PriceList.id == list_id).update({"is_default": True})
    db.commit()
    return _redir("/sales/pricing/lists?msg=Default+price+list+updated")


@router.get("/sales/pricing/lists/{list_id}/items", response_class=HTMLResponse)
def pricing_list_items(
    request: Request,
    list_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    pl = get_price_list_or_404(db, list_id, user.tenant_id)
    items = db.query(PriceListItem).filter(
        PriceListItem.price_list_id == list_id,
    ).all()
    items_by_product = {it.product_id: it for it in items}

    products = db.query(Product).filter(
        Product.tenant_id == user.tenant_id, Product.is_deleted == False,
        Product.is_active == True,
    ).order_by(Product.name).all()

    return templates.TemplateResponse(request, "sales/pricing_list_items.html", _ctx(
        db, user, price_list=pl, products=products, items_by_product=items_by_product,
        is_admin=user.role in ("ADMIN", "MANAGER"),
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/sales/pricing/lists/{list_id}/items/set")
def pricing_list_item_set(
    list_id: str,
    product_id: str = Form(...),
    unit_price: str = Form(...),
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    get_price_list_or_404(db, list_id, user.tenant_id)
    product = db.query(Product).filter(
        Product.id == product_id, Product.tenant_id == user.tenant_id,
    ).first()
    if not product:
        return _redir(f"/sales/pricing/lists/{list_id}/items?err=Invalid+product")
    try:
        price = float(unit_price)
        if price <= 0:
            raise ValueError
    except ValueError:
        return _redir(f"/sales/pricing/lists/{list_id}/items?err=Price+must+be+a+positive+number")

    set_price_list_item(db, list_id, product_id, price, user.tenant_id, user.id)
    return _redir(f"/sales/pricing/lists/{list_id}/items?msg=Price+updated")


@router.post("/sales/pricing/lists/{list_id}/items/{item_id}/delete")
def pricing_list_item_delete(
    list_id: str,
    item_id: str,
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    get_price_list_or_404(db, list_id, user.tenant_id)
    item = db.query(PriceListItem).filter(
        PriceListItem.id == item_id, PriceListItem.price_list_id == list_id,
    ).first()
    if item:
        db.delete(item)
        db.commit()
    return _redir(f"/sales/pricing/lists/{list_id}/items?msg=Item+removed")


@router.get("/sales/pricing/lists/{list_id}/bulk-template")
def pricing_list_bulk_template(
    list_id: str,
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    get_price_list_or_404(db, list_id, user.tenant_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["sku_code", "unit_price"])
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=price_list_template.csv"},
    )


@router.get("/sales/pricing/lists/{list_id}/bulk-upload", response_class=HTMLResponse)
def pricing_list_bulk_upload_form(
    request: Request,
    list_id: str,
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    pl = get_price_list_or_404(db, list_id, user.tenant_id)
    return templates.TemplateResponse(request, "sales/pricing_list_bulk.html", _ctx(
        db, user, price_list=pl,
    ))


@router.post("/sales/pricing/lists/{list_id}/bulk-upload")
async def pricing_list_bulk_upload(
    list_id: str,
    file: UploadFile = File(...),
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    get_price_list_or_404(db, list_id, user.tenant_id)
    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))

    applied = 0
    errors = []
    for i, row in enumerate(reader, start=2):
        sku = (row.get("sku_code") or "").strip()
        price_raw = (row.get("unit_price") or "").strip()
        if not sku:
            errors.append({"row": i, "error": "sku_code is required"})
            continue
        product = db.query(Product).filter(
            Product.tenant_id == user.tenant_id, Product.sku_code == sku,
            Product.is_deleted == False,
        ).first()
        if not product:
            errors.append({"row": i, "error": f"SKU {sku} not found"})
            continue
        try:
            price = float(price_raw)
            if price <= 0:
                raise ValueError
        except ValueError:
            errors.append({"row": i, "error": "unit_price must be a positive number"})
            continue
        set_price_list_item(db, list_id, product.id, price, user.tenant_id, user.id)
        applied += 1

    return JSONResponse({"applied": applied, "errors": errors})


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMER OVERRIDES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/pricing/overrides", response_class=HTMLResponse)
def pricing_overrides(
    request: Request,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    overrides = db.query(CustomerPriceOverride).filter(
        CustomerPriceOverride.tenant_id == user.tenant_id,
        CustomerPriceOverride.is_active == True,
    ).order_by(CustomerPriceOverride.created_at.desc()).all()

    customers = db.query(Customer).filter(
        Customer.tenant_id == user.tenant_id, Customer.is_deleted == False,
        Customer.is_active == True,
    ).order_by(Customer.name).all()
    products = db.query(Product).filter(
        Product.tenant_id == user.tenant_id, Product.is_deleted == False,
        Product.is_active == True,
    ).order_by(Product.name).all()

    return templates.TemplateResponse(request, "sales/pricing_overrides.html", _ctx(
        db, user, overrides=overrides, customers=customers, products=products,
        is_admin=user.role in ("ADMIN", "MANAGER"),
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/sales/pricing/overrides/create")
def pricing_override_create(
    customer_id: str = Form(...),
    product_id: str = Form(...),
    unit_price: str = Form(...),
    valid_from: str = Form(""),
    valid_to: str = Form(""),
    reason: str = Form(""),
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    customer = db.query(Customer).filter(
        Customer.id == customer_id, Customer.tenant_id == user.tenant_id,
    ).first()
    product = db.query(Product).filter(
        Product.id == product_id, Product.tenant_id == user.tenant_id,
    ).first()
    if not customer or not product:
        return _redir("/sales/pricing/overrides?err=Invalid+customer+or+product")
    try:
        price = float(unit_price)
        if price <= 0:
            raise ValueError
    except ValueError:
        return _redir("/sales/pricing/overrides?err=Price+must+be+a+positive+number")

    override = CustomerPriceOverride(
        tenant_id=user.tenant_id, customer_id=customer_id, product_id=product_id,
        unit_price=price,
        valid_from=date.fromisoformat(valid_from) if valid_from.strip() else None,
        valid_to=date.fromisoformat(valid_to) if valid_to.strip() else None,
        reason=reason.strip() or None, created_by_id=user.id,
    )
    db.add(override)
    db.commit()
    return _redir("/sales/pricing/overrides?msg=Override+created")


@router.post("/sales/pricing/overrides/{override_id}/revoke")
def pricing_override_revoke(
    override_id: str,
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    override = db.query(CustomerPriceOverride).filter(
        CustomerPriceOverride.id == override_id, CustomerPriceOverride.tenant_id == user.tenant_id,
    ).first()
    if not override:
        return _redir("/sales/pricing/overrides?err=Override+not+found")
    override.is_active = False
    db.commit()
    return _redir("/sales/pricing/overrides?msg=Override+revoked")


# ══════════════════════════════════════════════════════════════════════════════
# COST ENTRIES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/pricing/costs", response_class=HTMLResponse)
def pricing_costs(
    request: Request,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    entries = db.query(CostEntry).filter(
        CostEntry.tenant_id == user.tenant_id,
    ).order_by(CostEntry.effective_date.desc(), CostEntry.created_at.desc()).limit(200).all()

    products = db.query(Product).filter(
        Product.tenant_id == user.tenant_id, Product.is_deleted == False,
        Product.is_active == True,
    ).order_by(Product.name).all()

    return templates.TemplateResponse(request, "sales/pricing_costs.html", _ctx(
        db, user, entries=entries, products=products, cost_types=COST_TYPES,
        is_admin=user.role in ("ADMIN", "MANAGER"),
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/sales/pricing/costs/add")
def pricing_cost_add(
    product_id: str = Form(...),
    cost_type: str = Form(...),
    amount: str = Form(...),
    effective_date: str = Form(...),
    notes: str = Form(""),
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    product = db.query(Product).filter(
        Product.id == product_id, Product.tenant_id == user.tenant_id,
    ).first()
    if not product:
        return _redir("/sales/pricing/costs?err=Invalid+product")
    if cost_type not in COST_TYPES:
        return _redir("/sales/pricing/costs?err=Invalid+cost+type")
    try:
        amt = float(amount)
        if amt <= 0:
            raise ValueError
    except ValueError:
        return _redir("/sales/pricing/costs?err=Amount+must+be+a+positive+number")
    try:
        eff_date = date.fromisoformat(effective_date)
    except ValueError:
        return _redir("/sales/pricing/costs?err=Invalid+effective+date")

    db.add(CostEntry(
        tenant_id=user.tenant_id, product_id=product_id, cost_type=cost_type,
        amount=amt, effective_date=eff_date, notes=notes.strip() or None, actor_id=user.id,
    ))
    db.commit()
    return _redir("/sales/pricing/costs?msg=Cost+entry+added")


@router.get("/sales/pricing/costs/bulk-template")
def pricing_costs_bulk_template(
    user: User = Depends(_require_pricing_admin),
):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["sku_code", "cost_type", "amount", "effective_date", "notes"])
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cost_entries_template.csv"},
    )


@router.post("/sales/pricing/costs/bulk-upload")
async def pricing_costs_bulk_upload(
    file: UploadFile = File(...),
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))

    applied = 0
    errors = []
    for i, row in enumerate(reader, start=2):
        sku = (row.get("sku_code") or "").strip()
        cost_type = (row.get("cost_type") or "").strip().upper()
        amount_raw = (row.get("amount") or "").strip()
        date_raw = (row.get("effective_date") or "").strip()
        notes = (row.get("notes") or "").strip() or None

        if not sku:
            errors.append({"row": i, "error": "sku_code is required"})
            continue
        product = db.query(Product).filter(
            Product.tenant_id == user.tenant_id, Product.sku_code == sku,
            Product.is_deleted == False,
        ).first()
        if not product:
            errors.append({"row": i, "error": f"SKU {sku} not found"})
            continue
        if cost_type not in COST_TYPES:
            errors.append({"row": i, "error": f"cost_type must be one of {COST_TYPES}"})
            continue
        try:
            amt = float(amount_raw)
            if amt <= 0:
                raise ValueError
        except ValueError:
            errors.append({"row": i, "error": "amount must be a positive number"})
            continue
        try:
            eff_date = date.fromisoformat(date_raw)
        except ValueError:
            errors.append({"row": i, "error": "effective_date must be YYYY-MM-DD"})
            continue

        db.add(CostEntry(
            tenant_id=user.tenant_id, product_id=product.id, cost_type=cost_type,
            amount=amt, effective_date=eff_date, notes=notes, actor_id=user.id,
        ))
        applied += 1

    db.commit()
    return JSONResponse({"applied": applied, "errors": errors})


@router.get("/sales/pricing/costs/export")
def pricing_costs_export(
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    entries = db.query(CostEntry).filter(
        CostEntry.tenant_id == user.tenant_id,
    ).order_by(CostEntry.effective_date.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["sku_code", "product_name", "cost_type", "amount", "effective_date", "notes"])
    for e in entries:
        writer.writerow([
            e.product.sku_code if e.product else "", e.product.name if e.product else "",
            e.cost_type, e.amount, e.effective_date.isoformat(), e.notes or "",
        ])
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cost_entries_export.csv"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# PRICE TRENDS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/pricing/trends/{product_id}")
def price_trends(
    product_id: str,
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
):
    """Returns JSON for charting buy price history and sell price history."""
    buy_history = (
        db.query(CostEntry.effective_date, CostEntry.amount)
        .filter(CostEntry.product_id == product_id,
                CostEntry.tenant_id  == user.tenant_id,
                CostEntry.cost_type  == "BUY_PRICE")
        .order_by(CostEntry.effective_date.asc())
        .all()
    )

    sell_history_rows = (
        db.query(
            PriceListItemHistory.changed_at,
            PriceListItemHistory.new_price,
            PriceListItemHistory.price_list_name_snapshot,
        )
        .filter(PriceListItemHistory.product_id == product_id,
                PriceListItemHistory.tenant_id  == user.tenant_id)
        .order_by(PriceListItemHistory.changed_at.asc())
        .all()
    )

    sell_by_list = defaultdict(list)
    for row in sell_history_rows:
        sell_by_list[row.price_list_name_snapshot or "Unknown"].append({
            "date":  row.changed_at.date().isoformat(),
            "price": row.new_price,
        })

    return JSONResponse({
        "buy_price_history": [
            {"date": r.effective_date.isoformat(), "amount": r.amount}
            for r in buy_history
        ],
        "sell_price_history": dict(sell_by_list),
    })


# ══════════════════════════════════════════════════════════════════════════════
# MARGIN REPORT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/pricing/margin-report", response_class=HTMLResponse)
def margin_report(
    request: Request,
    user: User = Depends(_require_pricing_admin),
    db: Session = Depends(get_db),
    period: str = "30d",
    group_by: str = "product",
):
    cutoff_map = {"7d": 7, "30d": 30, "90d": 90}
    cutoff = (
        datetime.utcnow() - timedelta(days=cutoff_map[period])
        if period in cutoff_map else None
    )

    if group_by == "customer":
        group_col = SalesOrder.customer_id
    elif group_by == "agent":
        group_col = SalesOrder.agent_id
    else:
        group_by = "product"
        group_col = SalesOrderItem.product_id

    filters = [
        SalesOrder.tenant_id  == user.tenant_id,
        SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
        SalesOrder.is_deleted == False,
    ]
    if cutoff:
        filters.append(SalesOrder.created_at >= cutoff)

    query = (
        db.query(
            group_col,
            func.sum(SalesOrderItem.line_total).label("total_revenue"),
            func.sum(
                func.coalesce(SalesOrderItem.cost_snapshot, 0) * SalesOrderItem.qty_ordered
            ).label("total_cost"),
            func.count(SalesOrder.id.distinct()).label("order_count"),
        )
        .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
        .filter(*filters)
        .group_by(group_col)
        .order_by(func.sum(SalesOrderItem.line_total).desc())
        .all()
    )

    entity_name_fn = None
    if group_by == "product":
        entity_name_fn = lambda eid: (db.query(Product.name).filter(Product.id == eid).scalar() or eid)
    elif group_by == "customer":
        entity_name_fn = lambda eid: (db.query(Customer.name).filter(Customer.id == eid).scalar() or eid)
    else:
        entity_name_fn = lambda eid: (db.query(User.name).filter(User.id == eid).scalar() or eid)

    results = []
    for row in query:
        revenue = row.total_revenue or 0
        cost    = row.total_cost    or 0
        margin  = (revenue - cost) / revenue * 100 if revenue > 0 else None
        results.append({
            "entity_id":     row[0],
            "entity_name":   entity_name_fn(row[0]) if row[0] else "—",
            "total_revenue": revenue,
            "total_cost":    cost,
            "gross_margin":  margin,
            "order_count":   row.order_count,
        })

    return templates.TemplateResponse(request, "sales/pricing_margin_report.html", _ctx(
        db, user, results=results, group_by=group_by, period=period,
    ))
