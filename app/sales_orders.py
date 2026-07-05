"""
Sales Orders — Brief 05.
Order CRUD (header + line items), stock reservation engine wiring,
dispatch/delivery flow, bulk order creation, CSV export.
Line items reference ProductVariant (the sellable SKU) — see Catalog
Hierarchy Phase 1. A Product alone isn't sellable; the order picker is a
two-step Product -> Variant flow.
"""
import csv
import io
import calendar
from datetime import datetime, date
from datetime import date as _date

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import func, or_ as _or
from sqlalchemy.orm import Session
from typing import Optional

from .database import (
    get_db, new_id, User, Customer, Product, ProductVariant, UnitOfMeasure, ProductStock,
    InventoryPurchaseOrder, InventoryPOItem, SalesOrder, SalesOrderItem,
    PriceList, PriceListItem, CustomerPriceOverride, Branch, MediaUpload, SalesTarget,
)
from .auth import get_current_user, has_module, require_module
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread
from .sales_inventory import reserve_stock_for_item, release_all_reservations, fulfill_reservation
from .constants import SALES_MARGIN_FLOOR_PCT, BULK_IMPORT_MAX_ROWS
from .uploads import save_upload

router = APIRouter()

_require_sales = require_module("SALES", "SALES_MODULE")


def _ctx(db: Session, user: User, **extra) -> dict:
    ctx = {
        "user": user, "L": _L(db, user), "unread": _unread(db, user),
    }
    ctx.update(_nav_ctx(db, user))
    ctx.update(extra)
    return ctx


def _redir(url: str):
    return RedirectResponse(url, status_code=303)


def get_order_or_404(db: Session, order_id: str, tenant_id: str) -> SalesOrder:
    order = db.query(SalesOrder).filter(
        SalesOrder.id == order_id,
        SalesOrder.tenant_id == tenant_id,
        SalesOrder.is_deleted == False,
    ).first()
    if not order:
        raise HTTPException(404, "Order not found")
    return order


def _can_view_order(user: User, order: SalesOrder) -> bool:
    if user.role in ("ADMIN", "MANAGER"):
        return True
    return order.agent_id == user.id


def generate_order_display_id(db, tenant_id: str) -> str:
    count = db.query(func.count(SalesOrder.id)).filter(
        SalesOrder.tenant_id == tenant_id,
        SalesOrder.is_deleted == False,
    ).scalar() or 0
    return f"SO-{str(count + 1).zfill(4)}"


def _sales_agents(db: Session, tenant_id: str) -> list:
    """Users with SALES module access — used for the Salesman dropdown."""
    return [u for u in db.query(User).filter(
        User.tenant_id == tenant_id, User.is_active == True, User.is_deleted == False,
    ).order_by(User.name).all() if has_module(u, "SALES")]


def _active_branches(db: Session, tenant_id: str) -> list:
    return db.query(Branch).filter(
        Branch.tenant_id == tenant_id, Branch.is_deleted == False,
    ).order_by(Branch.name).all()


def resolve_price(db, customer_id: str, variant_id: str, tenant_id: str) -> dict:
    """
    Per-line-item price resolution — Brief 06. Checks three levels in order.
    Returns {"price": float | None, "source": str}

    Resolution order:
      1. Active customer-specific override for (customer_id, variant_id)
      2. Price list assigned to this customer (customer.price_list_id)
      3. Tenant's default price list
      4. None — agent must enter manual price
    """
    today = _date.today()

    # Level 1: customer-specific override
    override = (
        db.query(CustomerPriceOverride)
        .filter(
            CustomerPriceOverride.customer_id == customer_id,
            CustomerPriceOverride.variant_id  == variant_id,
            CustomerPriceOverride.tenant_id   == tenant_id,
            CustomerPriceOverride.is_active   == True,
            _or(CustomerPriceOverride.valid_from == None,
                CustomerPriceOverride.valid_from <= today),
            _or(CustomerPriceOverride.valid_to   == None,
                CustomerPriceOverride.valid_to   >= today),
        )
        .order_by(CustomerPriceOverride.created_at.desc())
        .first()
    )
    if override:
        return {"price": override.unit_price, "source": "CUSTOMER_OVERRIDE"}

    # Level 2: customer's assigned price list
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer and customer.price_list_id:
        pli = (
            db.query(PriceListItem)
            .join(PriceList, PriceListItem.price_list_id == PriceList.id)
            .filter(
                PriceListItem.price_list_id == customer.price_list_id,
                PriceListItem.variant_id    == variant_id,
                PriceListItem.is_active     == True,
                PriceList.is_active         == True,
                _or(PriceList.valid_from == None, PriceList.valid_from <= today),
                _or(PriceList.valid_to   == None, PriceList.valid_to   >= today),
            )
            .first()
        )
        if pli:
            return {"price": pli.unit_price, "source": "PRICE_LIST"}

    # Level 3: default price list
    default_list = (
        db.query(PriceList)
        .filter(PriceList.tenant_id  == tenant_id,
                PriceList.is_default == True,
                PriceList.is_active  == True)
        .first()
    )
    if default_list:
        pli = (
            db.query(PriceListItem)
            .filter(PriceListItem.price_list_id == default_list.id,
                    PriceListItem.variant_id    == variant_id,
                    PriceListItem.is_active     == True)
            .first()
        )
        if pli:
            return {"price": pli.unit_price, "source": "DEFAULT_LIST"}

    return {"price": None, "source": "NONE"}


def check_margin(sell_price: float, cost_snapshot: float) -> bool:
    """Returns True if margin is acceptable (above floor)."""
    if not cost_snapshot or cost_snapshot == 0:
        return True  # Can't compute margin without cost; allow and flag
    margin = (sell_price - cost_snapshot) / sell_price * 100
    return margin >= SALES_MARGIN_FLOOR_PCT


# ══════════════════════════════════════════════════════════════════════════════
# SALES TARGETS — 1.9. Admin/Manager sets a revenue (+ optional order count)
# target per agent per period; actuals are computed on read from SalesOrder,
# never stored redundantly, to avoid drift. Bonus-formula calculation itself
# is an open question per the brief — not implemented here.
# ══════════════════════════════════════════════════════════════════════════════

def _period_bounds(period_label: str):
    """'YYYY-MM' -> (start_datetime, end_datetime_exclusive)."""
    year, month = (int(p) for p in period_label.split("-"))
    start = datetime(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59)
    return start, end


def _actuals_for_agent(db: Session, tenant_id: str, agent_id: str, period_label: str) -> dict:
    start, end = _period_bounds(period_label)
    row = (
        db.query(
            func.coalesce(func.sum(SalesOrder.total_amount), 0.0).label("amount"),
            func.count(SalesOrder.id).label("orders"),
        )
        .filter(
            SalesOrder.tenant_id == tenant_id, SalesOrder.agent_id == agent_id,
            SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
            SalesOrder.is_deleted == False,
            SalesOrder.created_at >= start, SalesOrder.created_at <= end,
        )
        .first()
    )
    return {"actual_amount": row.amount or 0.0, "actual_orders": row.orders or 0}


@router.get("/sales/orders/targets", response_class=HTMLResponse)
def sales_targets_view(
    request: Request,
    period: str = "",
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    period_label = period or datetime.utcnow().strftime("%Y-%m")
    is_admin = user.role in ("ADMIN", "MANAGER")

    agent_ids = [user.id]
    if is_admin:
        agent_ids = [a.id for a in _sales_agents(db, user.tenant_id)]

    targets = db.query(SalesTarget).filter(
        SalesTarget.tenant_id == user.tenant_id, SalesTarget.period_label == period_label,
        SalesTarget.agent_id.in_(agent_ids),
    ).all()
    targets_by_agent = {t.agent_id: t for t in targets}

    rows = []
    for agent in (_sales_agents(db, user.tenant_id) if is_admin else [user]):
        if agent.id not in agent_ids:
            continue
        target = targets_by_agent.get(agent.id)
        actuals = _actuals_for_agent(db, user.tenant_id, agent.id, period_label)
        target_amount = target.target_amount if target else None
        attainment_pct = (
            (actuals["actual_amount"] / target_amount * 100) if target_amount else None
        )
        rows.append({
            "agent": agent, "target": target, "target_amount": target_amount,
            "target_orders": target.target_orders if target else None,
            "actual_amount": actuals["actual_amount"], "actual_orders": actuals["actual_orders"],
            "attainment_pct": attainment_pct,
        })

    return templates.TemplateResponse(request, "sales/orders_targets.html", _ctx(
        db, user, rows=rows, period_label=period_label, is_admin=is_admin,
        agents=_sales_agents(db, user.tenant_id) if is_admin else [],
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.post("/sales/orders/targets/set")
def sales_target_set(
    agent_id: str = Form(...),
    period_label: str = Form(...),
    target_amount: str = Form(...),
    target_orders: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(403, "Admin/Manager only")

    agent = db.query(User).filter(
        User.id == agent_id, User.tenant_id == user.tenant_id,
        User.is_active == True, User.is_deleted == False,
    ).first()
    if not agent or not has_module(agent, "SALES"):
        return _redir(f"/sales/orders/targets?period={period_label}&err=Invalid+agent")

    try:
        amount = float(target_amount)
        if amount <= 0:
            raise ValueError
    except ValueError:
        return _redir(f"/sales/orders/targets?period={period_label}&err=Target+amount+must+be+a+positive+number")

    orders_target = None
    if target_orders.strip():
        try:
            orders_target = int(target_orders)
        except ValueError:
            return _redir(f"/sales/orders/targets?period={period_label}&err=Target+orders+must+be+a+whole+number")

    existing = db.query(SalesTarget).filter(
        SalesTarget.tenant_id == user.tenant_id, SalesTarget.agent_id == agent_id,
        SalesTarget.period_label == period_label,
    ).first()
    if existing:
        existing.target_amount = amount
        existing.target_orders = orders_target
    else:
        db.add(SalesTarget(
            tenant_id=user.tenant_id, agent_id=agent_id, period_label=period_label,
            target_amount=amount, target_orders=orders_target, created_by_id=user.id,
        ))
    db.commit()
    return _redir(f"/sales/orders/targets?period={period_label}&msg=Target+saved")


# ══════════════════════════════════════════════════════════════════════════════
# ORDER LIST / CREATE
# ══════════════════════════════════════════════════════════════════════════════

STATUS_CHOICES = ("DRAFT", "CONFIRMED", "DISPATCHED", "DELIVERED", "CANCELLED")
PAGE_SIZE = 30


@router.get("/sales/orders", response_class=HTMLResponse)
def orders_list(
    request: Request,
    status: str = "",
    page: int = 1,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    q = db.query(SalesOrder).filter(
        SalesOrder.tenant_id == user.tenant_id,
        SalesOrder.is_deleted == False,
    )
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(SalesOrder.agent_id == user.id)
    if status and status in STATUS_CHOICES:
        q = q.filter(SalesOrder.status == status)

    q = q.order_by(SalesOrder.created_at.desc())
    total = q.count()
    orders = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    return templates.TemplateResponse(request, "sales/orders_list.html", _ctx(
        db, user,
        orders=orders, total=total, page=page, page_size=PAGE_SIZE,
        status=status, status_choices=STATUS_CHOICES,
    ))


@router.get("/sales/orders/new", response_class=HTMLResponse)
def order_new_form(
    request: Request,
    customer_id: str = "",
    call_log_id: str = "",
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    customer = None
    if customer_id:
        customer = db.query(Customer).filter(
            Customer.id == customer_id, Customer.tenant_id == user.tenant_id,
            Customer.is_deleted == False,
        ).first()

    customers = []
    if user.role in ("ADMIN", "MANAGER"):
        customers = db.query(Customer).filter(
            Customer.tenant_id == user.tenant_id, Customer.is_deleted == False,
            Customer.is_active == True,
        ).order_by(Customer.name).all()
    else:
        customers = db.query(Customer).filter(
            Customer.tenant_id == user.tenant_id, Customer.is_deleted == False,
            Customer.is_active == True, Customer.assigned_agent_id == user.id,
        ).order_by(Customer.name).all()

    return templates.TemplateResponse(request, "sales/orders_new.html", _ctx(
        db, user, customer=customer, customers=customers, call_log_id=call_log_id,
        agents=_sales_agents(db, user.tenant_id), branches=_active_branches(db, user.tenant_id),
    ))


@router.get("/sales/orders/api/customer-defaults")
def order_customer_defaults_api(
    customer_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    """Returns a customer's default payment terms + price group for order-form prefill."""
    customer = db.query(Customer).filter(
        Customer.id == customer_id, Customer.tenant_id == user.tenant_id,
        Customer.is_deleted == False,
    ).first()
    if not customer:
        return JSONResponse({"error": "not found"}, status_code=404)
    price_list = db.query(PriceList).filter(PriceList.id == customer.price_list_id).first() if customer.price_list_id else None
    return JSONResponse({
        "default_payment_terms": customer.default_payment_terms,
        "price_list_id": customer.price_list_id,
        "price_list_name": price_list.name if price_list else None,
        "shipping_address": customer.shipping_address,
    })


@router.post("/sales/orders/quick-customer")
def order_quick_customer_create(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    """Inline 'create new customer' affordance on the order screen (1.3) —
    lets an agent add a new party without leaving the order-creation flow."""
    if not name.strip():
        return _redir("/sales/orders/new?err=Party+name+is+required")

    customer = Customer(
        tenant_id=user.tenant_id,
        name=name.strip(),
        phone=phone.strip() or None,
        email=email.strip() or None,
        created_by_id=user.id,
        assigned_agent_id=user.id,
    )
    db.add(customer)
    db.commit()
    return _redir(f"/sales/orders/new?customer_id={customer.id}&msg=Customer+created")


@router.post("/sales/orders/create")
def order_create(
    customer_id: str = Form(...),
    agent_id: str = Form(""),
    call_log_id: str = Form(""),
    payment_terms: str = Form(""),
    delivery_address: str = Form(""),
    branch_id: str = Form(""),
    expected_delivery_date: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    customer = db.query(Customer).filter(
        Customer.id == customer_id, Customer.tenant_id == user.tenant_id,
        Customer.is_deleted == False,
    ).first()
    if not customer:
        return _redir("/sales/orders/new?err=Invalid+customer")
    if user.role not in ("ADMIN", "MANAGER") and customer.assigned_agent_id != user.id:
        raise HTTPException(403, "Not your assigned customer")

    resolved_agent_id = user.id
    if agent_id and agent_id != user.id:
        agent = db.query(User).filter(
            User.id == agent_id, User.tenant_id == user.tenant_id,
            User.is_active == True, User.is_deleted == False,
        ).first()
        if not agent or not has_module(agent, "SALES"):
            return _redir("/sales/orders/new?err=Invalid+salesman")
        resolved_agent_id = agent.id

    resolved_branch_id = None
    if branch_id:
        branch = db.query(Branch).filter(
            Branch.id == branch_id, Branch.tenant_id == user.tenant_id, Branch.is_deleted == False,
        ).first()
        if not branch:
            return _redir("/sales/orders/new?err=Invalid+branch")
        resolved_branch_id = branch.id

    edd = None
    if expected_delivery_date:
        try:
            edd = date.fromisoformat(expected_delivery_date)
        except ValueError:
            edd = None

    order = SalesOrder(
        display_id=generate_order_display_id(db, user.tenant_id),
        tenant_id=user.tenant_id,
        customer_id=customer_id,
        agent_id=resolved_agent_id,
        status="DRAFT",
        payment_terms=payment_terms.strip() or customer.default_payment_terms,
        delivery_address=delivery_address.strip() or customer.shipping_address,
        branch_id=resolved_branch_id,
        expected_delivery_date=edd,
        notes=notes.strip() or None,
        call_log_id=call_log_id or None,
    )
    db.add(order)
    db.commit()
    return _redir(f"/sales/orders/{order.id}?msg=Order+created")


# ══════════════════════════════════════════════════════════════════════════════
# ORDER DETAIL / LINE ITEMS
# ══════════════════════════════════════════════════════════════════════════════
# STATIC GET PATHS — must be declared before the dynamic /{order_id} route below
# ══════════════════════════════════════════════════════════════════════════════

_BULK_COLS = ["customer_phone", "product_sku", "qty", "unit_abbreviation",
              "manual_price", "expected_delivery_date", "notes",
              "salesman_email", "branch_name"]


@router.get("/sales/orders/bulk-template")
def bulk_template(user: User = Depends(_require_sales)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_BULK_COLS)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=orders_bulk_template.csv"},
    )


@router.get("/sales/orders/bulk-upload", response_class=HTMLResponse)
def bulk_upload_form(request: Request, user: User = Depends(_require_sales), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "sales/orders_bulk_upload.html", _ctx(db, user))


@router.get("/sales/orders/export")
def orders_export(user: User = Depends(_require_sales), db: Session = Depends(get_db)):
    q = db.query(SalesOrder).filter(
        SalesOrder.tenant_id == user.tenant_id, SalesOrder.is_deleted == False,
    )
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(SalesOrder.agent_id == user.id)

    orders = q.order_by(SalesOrder.created_at.desc()).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "display_id", "customer_name", "agent_name", "status", "created_at",
        "expected_delivery_date", "total_amount", "total_cost", "gross_margin_pct",
        "item_count", "confirmed_at", "dispatched_at",
    ])
    for o in orders:
        w.writerow([
            o.display_id or "", o.customer.name if o.customer else "",
            o.agent.name if o.agent else "", o.status,
            o.created_at.isoformat() if o.created_at else "",
            o.expected_delivery_date.isoformat() if o.expected_delivery_date else "",
            o.total_amount or 0, o.total_cost or 0,
            f"{o.gross_margin_pct:.1f}" if o.gross_margin_pct is not None else "",
            len(o.items), o.confirmed_at.isoformat() if o.confirmed_at else "",
            o.dispatched_at.isoformat() if o.dispatched_at else "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=orders_export.csv"},
    )


# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/orders/{order_id}", response_class=HTMLResponse)
def order_detail(
    request: Request,
    order_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")

    # Two-step Product -> Variant picker: a Product with zero variants isn't sellable.
    products = (
        db.query(Product)
        .filter(Product.tenant_id == user.tenant_id, Product.is_deleted == False, Product.is_active == True)
        .order_by(Product.name)
        .all()
    )
    products = [p for p in products if any(v.is_active and not v.is_deleted for v in p.variants)]

    return templates.TemplateResponse(request, "sales/order_detail.html", _ctx(
        db, user, order=order, products=products,
        can_edit=(order.status == "DRAFT" and _can_view_order(user, order)),
        can_dispatch=(user.role in ("ADMIN", "MANAGER") or has_module(user, "INVENTORY")),
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.get("/sales/orders/api/resolve-price")
def order_resolve_price_api(
    customer_id: str,
    variant_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    result = resolve_price(db, customer_id, variant_id, user.tenant_id)
    return JSONResponse(result)


def _preview_stock_status(db, tenant_id: str, variant_id: str, qty: float) -> tuple:
    """
    Non-blocking stock check for add-item/update-item (1.5). Out-of-stock items
    are still accepted onto a DRAFT order — this only computes the badge shown
    on the order screen. The real reservation attempt happens at order_confirm().
    Returns (stock_status, in_transit_date | None).
    """
    stock = db.query(ProductStock).filter(
        ProductStock.variant_id == variant_id, ProductStock.tenant_id == tenant_id,
    ).first()
    available = stock.qty_available if stock else 0
    if qty <= available:
        return "AVAILABLE", None

    in_transit_date = (
        db.query(func.min(InventoryPurchaseOrder.expected_arrival_date))
        .join(InventoryPOItem, InventoryPOItem.po_id == InventoryPurchaseOrder.id)
        .filter(
            InventoryPOItem.variant_id == variant_id,
            InventoryPurchaseOrder.tenant_id == tenant_id,
            InventoryPurchaseOrder.status.in_(["SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]),
        )
        .scalar()
    )
    return "UNAVAILABLE", in_transit_date


@router.post("/sales/orders/{order_id}/add-item")
async def order_add_item(
    order_id: str,
    variant_id: str = Form(...),
    qty_ordered: str = Form(...),
    manual_override_price: str = Form(""),
    override_reason: str = Form(""),
    photo: UploadFile = File(None),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")
    if order.status != "DRAFT":
        return _redir(f"/sales/orders/{order_id}?err=Only+DRAFT+orders+can+be+edited")

    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id, ProductVariant.tenant_id == user.tenant_id,
        ProductVariant.is_deleted == False,
    ).first()
    if not variant:
        return _redir(f"/sales/orders/{order_id}?err=Invalid+variant")

    try:
        qty = float(qty_ordered)
        if qty <= 0:
            raise ValueError
    except ValueError:
        return _redir(f"/sales/orders/{order_id}?err=Invalid+quantity")

    preview_status, preview_in_transit = _preview_stock_status(db, user.tenant_id, variant_id, qty)

    price_info = resolve_price(db, order.customer_id, variant_id, user.tenant_id)
    price = price_info["price"]
    price_source = price_info["source"]

    if price is None:
        if not manual_override_price:
            return _redir(f"/sales/orders/{order_id}?err=No+price+configured+-+enter+a+manual+price")
        try:
            price = float(manual_override_price)
        except ValueError:
            return _redir(f"/sales/orders/{order_id}?err=Invalid+manual+price")
        price_source = "MANUAL"

    stock = db.query(ProductStock).filter(ProductStock.variant_id == variant_id).first()
    cost_snapshot = stock.avg_cost if stock else None

    approval_status = None
    if price_source == "MANUAL" and not check_margin(price, cost_snapshot):
        approval_status = "PENDING"

    unit_id = variant.base_unit_id or (variant.product.base_unit_id if variant.product else None)

    item = SalesOrderItem(
        order_id=order_id,
        tenant_id=user.tenant_id,
        variant_id=variant_id,
        qty_ordered=qty,
        unit_id=unit_id,
        unit_price=price,
        price_source=price_source,
        manual_override_price=float(manual_override_price) if manual_override_price else None,
        override_reason=override_reason.strip() or None,
        approval_status=approval_status,
        line_total=qty * price,
        stock_status=preview_status,
        in_transit_arrival=preview_in_transit,
    )
    db.add(item)
    db.flush()

    if photo is not None and (photo.filename or ""):
        result = await save_upload(photo, user.tenant_id)
        db.add(MediaUpload(
            tenant_id=user.tenant_id,
            entity_type="sales_order_item",
            entity_id=item.id,
            file_name=result["file_name"],
            file_path=result["file_path"],
            file_type=result["file_type"],
            file_size=result["file_size"],
            uploaded_by_id=user.id,
        ))

    db.commit()
    msg = "Item+added" if preview_status == "AVAILABLE" else "Item+added+-+out+of+stock%2C+arrange+separately"
    return _redir(f"/sales/orders/{order_id}?msg={msg}")


@router.post("/sales/orders/{order_id}/update-item/{item_id}")
def order_update_item(
    order_id: str,
    item_id: str,
    qty_ordered: str = Form(...),
    manual_override_price: str = Form(""),
    override_reason: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")
    if order.status != "DRAFT":
        return _redir(f"/sales/orders/{order_id}?err=Only+DRAFT+orders+can+be+edited")

    item = db.query(SalesOrderItem).filter(
        SalesOrderItem.id == item_id, SalesOrderItem.order_id == order_id,
    ).first()
    if not item:
        return _redir(f"/sales/orders/{order_id}?err=Item+not+found")

    try:
        qty = float(qty_ordered)
        if qty <= 0:
            raise ValueError
    except ValueError:
        return _redir(f"/sales/orders/{order_id}?err=Invalid+quantity")

    item.stock_status, item.in_transit_arrival = _preview_stock_status(db, user.tenant_id, item.variant_id, qty)
    item.qty_ordered = qty

    if manual_override_price:
        try:
            price = float(manual_override_price)
        except ValueError:
            return _redir(f"/sales/orders/{order_id}?err=Invalid+manual+price")
        item.unit_price = price
        item.manual_override_price = price
        item.price_source = "MANUAL"
        item.override_reason = override_reason.strip() or None

        stock = db.query(ProductStock).filter(ProductStock.variant_id == item.variant_id).first()
        cost_snapshot = stock.avg_cost if stock else None
        item.approval_status = "PENDING" if not check_margin(price, cost_snapshot) else None

    item.line_total = item.qty_ordered * item.unit_price
    db.commit()
    return _redir(f"/sales/orders/{order_id}?msg=Item+updated")


@router.post("/sales/orders/{order_id}/remove-item/{item_id}")
def order_remove_item(
    order_id: str,
    item_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")
    if order.status != "DRAFT":
        return _redir(f"/sales/orders/{order_id}?err=Only+DRAFT+orders+can+be+edited")

    item = db.query(SalesOrderItem).filter(
        SalesOrderItem.id == item_id, SalesOrderItem.order_id == order_id,
    ).first()
    if item:
        db.delete(item)
        db.commit()
    return _redir(f"/sales/orders/{order_id}?msg=Item+removed")


@router.post("/sales/orders/{order_id}/item/{item_id}/upload-media")
async def order_item_upload_media(
    order_id: str,
    item_id: str,
    file: UploadFile = File(...),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    """Per-line-item reference photo/document (1.3) — separate from catalog
    photos, e.g. a custom print reference attached to just this order line."""
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")
    if order.status != "DRAFT":
        return _redir(f"/sales/orders/{order_id}?err=Only+DRAFT+orders+can+be+edited")

    item = db.query(SalesOrderItem).filter(
        SalesOrderItem.id == item_id, SalesOrderItem.order_id == order_id,
    ).first()
    if not item:
        return _redir(f"/sales/orders/{order_id}?err=Item+not+found")

    result = await save_upload(file, user.tenant_id)
    db.add(MediaUpload(
        tenant_id=user.tenant_id,
        entity_type="sales_order_item",
        entity_id=item_id,
        file_name=result["file_name"],
        file_path=result["file_path"],
        file_type=result["file_type"],
        file_size=result["file_size"],
        uploaded_by_id=user.id,
    ))
    db.commit()
    return _redir(f"/sales/orders/{order_id}?msg=Reference+file+attached")


@router.post("/sales/orders/{order_id}/item/{item_id}/media/{media_id}/delete")
def order_item_delete_media(
    order_id: str,
    item_id: str,
    media_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")
    if order.status != "DRAFT":
        return _redir(f"/sales/orders/{order_id}?err=Only+DRAFT+orders+can+be+edited")

    media = db.query(MediaUpload).filter(
        MediaUpload.id == media_id, MediaUpload.entity_type == "sales_order_item",
        MediaUpload.entity_id == item_id, MediaUpload.tenant_id == user.tenant_id,
    ).first()
    if media:
        db.delete(media)
        db.commit()
    return _redir(f"/sales/orders/{order_id}?msg=Reference+file+removed")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIRM / CANCEL / DISPATCH / DELIVER
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sales/orders/{order_id}/confirm")
def order_confirm(
    order_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")

    if order.status != "DRAFT":
        return _redir(f"/sales/orders/{order_id}?err=Only+DRAFT+orders+can+be+confirmed")

    if not order.items:
        return _redir(f"/sales/orders/{order_id}?err=Order+has+no+items")

    pending_items = [i for i in order.items if i.approval_status == "PENDING"]
    if pending_items and user.role not in ("ADMIN", "MANAGER"):
        return _redir(f"/sales/orders/{order_id}?err=Order+has+items+pending+Manager+price+approval")

    for item in order.items:
        stock = db.query(ProductStock).filter(
            ProductStock.variant_id == item.variant_id
        ).first()
        item.cost_snapshot = stock.avg_cost if stock else None

        result = reserve_stock_for_item(
            db, item.variant_id, order.id, item.id,
            item.qty_ordered, user.id, user.tenant_id,
        )

        if result["success"]:
            item.stock_status = "AVAILABLE"
        else:
            item.stock_status = "UNAVAILABLE"
            item.in_transit_arrival = result.get("in_transit_date")

    order.total_amount = sum(i.line_total for i in order.items)
    order.total_cost = sum((i.cost_snapshot or 0) * i.qty_ordered for i in order.items)
    order.gross_margin_pct = (
        (order.total_amount - order.total_cost) / order.total_amount * 100
        if order.total_amount > 0 else None
    )
    order.price_list_id_snapshot = order.customer.price_list_id

    order.status = "CONFIRMED"
    order.confirmed_at = datetime.utcnow()
    order.updated_at = datetime.utcnow()
    db.commit()

    from .notifications import notify_order_placed
    notify_order_placed(db, order)

    return _redir(f"/sales/orders/{order_id}?msg=Order+confirmed")


@router.post("/sales/orders/{order_id}/cancel")
def order_cancel(
    order_id: str,
    cancellation_reason: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")

    if order.status not in ("DRAFT", "CONFIRMED"):
        return _redir(f"/sales/orders/{order_id}?err=Order+cannot+be+cancelled+in+its+current+status")
    if order.status == "DRAFT" and user.role not in ("ADMIN", "MANAGER") and order.agent_id != user.id:
        raise HTTPException(403, "Not authorized")

    if order.status == "CONFIRMED":
        release_all_reservations(db, order.id, user.tenant_id, reason="Order cancelled")

    order.status = "CANCELLED"
    order.cancelled_at = datetime.utcnow()
    order.cancellation_reason = cancellation_reason.strip() or None
    order.updated_at = datetime.utcnow()
    db.commit()
    return _redir(f"/sales/orders/{order_id}?msg=Order+cancelled")


@router.post("/sales/orders/{order_id}/dispatch")
def order_dispatch(
    order_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role not in ("ADMIN", "MANAGER") and not has_module(user, "INVENTORY"):
        raise HTTPException(403, "Manager, Admin, or Inventory module access required")

    order = get_order_or_404(db, order_id, user.tenant_id)
    if order.status != "CONFIRMED":
        return _redir(f"/sales/orders/{order_id}?err=Only+CONFIRMED+orders+can+be+dispatched")

    for item in order.items:
        if item.stock_status in ("AVAILABLE", "PARTIAL"):
            fulfill_reservation(
                db, order.id, item.variant_id,
                item.qty_ordered, user.tenant_id, user.id,
            )
            item.qty_dispatched = item.qty_ordered

    order.status = "DISPATCHED"
    order.dispatched_at = datetime.utcnow()
    order.updated_at = datetime.utcnow()
    db.commit()

    from .notifications import notify_order_dispatched
    notify_order_dispatched(db, order, user)

    return _redir(f"/sales/orders/{order_id}?msg=Order+dispatched")


@router.post("/sales/orders/{order_id}/deliver")
def order_deliver(
    order_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    order = get_order_or_404(db, order_id, user.tenant_id)
    if not _can_view_order(user, order):
        raise HTTPException(403, "Not your order")
    if order.status != "DISPATCHED":
        return _redir(f"/sales/orders/{order_id}?err=Only+DISPATCHED+orders+can+be+marked+delivered")

    order.status = "DELIVERED"
    order.delivered_at = datetime.utcnow()
    order.updated_at = datetime.utcnow()
    db.commit()
    return _redir(f"/sales/orders/{order_id}?msg=Order+marked+delivered")


# ══════════════════════════════════════════════════════════════════════════════
# STOCK CHECK API
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/orders/api/check-stock/{variant_id}")
def api_check_stock(variant_id: str, user: User = Depends(_require_sales), db: Session = Depends(get_db)):
    """
    Returns JSON for inline stock status display on order form — 1.6.
    Surfaces all three numbers (available / already-booked / in-transit) so
    agents don't get a false sense of availability from qty_available alone.
    """
    stock = db.query(ProductStock).filter(
        ProductStock.variant_id == variant_id,
        ProductStock.tenant_id == user.tenant_id,
    ).first()

    if not stock:
        return JSONResponse({"available": 0, "reserved": 0,
                              "in_transit": 0, "in_transit_date": None})

    in_transit = (
        db.query(
            func.min(InventoryPurchaseOrder.expected_arrival_date).label("date")
        )
        .join(InventoryPOItem, InventoryPOItem.po_id == InventoryPurchaseOrder.id)
        .filter(
            InventoryPOItem.variant_id == variant_id,
            InventoryPurchaseOrder.tenant_id == user.tenant_id,
            InventoryPurchaseOrder.status.in_(["SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]),
        )
        .scalar()
    )

    return JSONResponse({
        "available": stock.qty_available,
        "reserved": stock.qty_reserved,
        "in_transit": stock.qty_in_transit,
        "in_transit_date": in_transit.isoformat() if in_transit else None,
    })


# ══════════════════════════════════════════════════════════════════════════════
# BULK ORDER CREATION  (GET form/template routes declared earlier, above)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sales/orders/bulk-upload")
async def bulk_upload(
    file: UploadFile = File(...),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    if (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload the CSV template, not an Excel file.")
    content = raw.decode("utf-8-sig", errors="replace").lstrip(chr(65279))
    try:
        reader = list(csv.DictReader(io.StringIO(content)))
    except csv.Error:
        raise HTTPException(400, "Could not parse file — please upload a valid CSV using the provided template.")
    if len(reader) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(reader)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")

    rows_by_phone: dict = {}
    errors = []

    for i, row in enumerate(reader, start=2):
        phone = (row.get("customer_phone") or "").strip()
        sku = (row.get("product_sku") or "").strip()
        qty_raw = (row.get("qty") or "").strip()

        if not phone:
            errors.append({"row": i, "error": "customer_phone is required", "data": dict(row)})
            continue
        if not sku:
            errors.append({"row": i, "error": "product_sku is required", "data": dict(row)})
            continue

        customer = db.query(Customer).filter(
            Customer.tenant_id == user.tenant_id, Customer.phone == phone,
            Customer.is_deleted == False,
        ).first()
        if not customer:
            errors.append({"row": i, "error": f"customer phone {phone} not found", "data": dict(row)})
            continue

        variant = db.query(ProductVariant).filter(
            ProductVariant.tenant_id == user.tenant_id, ProductVariant.sku_code == sku,
            ProductVariant.is_deleted == False,
        ).first()
        if not variant:
            errors.append({"row": i, "error": f"product SKU {sku} not found", "data": dict(row)})
            continue

        try:
            qty = float(qty_raw)
            if qty <= 0:
                raise ValueError
        except ValueError:
            errors.append({"row": i, "error": "qty must be a positive number", "data": dict(row)})
            continue

        manual_price = None
        if (row.get("manual_price") or "").strip():
            try:
                manual_price = float(row["manual_price"])
            except ValueError:
                errors.append({"row": i, "error": "manual_price must be a number", "data": dict(row)})
                continue

        salesman_email = (row.get("salesman_email") or "").strip()
        agent_id = None
        if salesman_email:
            agent = db.query(User).filter(
                User.tenant_id == user.tenant_id, User.email == salesman_email,
                User.is_active == True, User.is_deleted == False,
            ).first()
            if not agent or not has_module(agent, "SALES"):
                errors.append({"row": i, "error": f"salesman_email {salesman_email} not found or lacks SALES access", "data": dict(row)})
                continue
            agent_id = agent.id

        branch_name = (row.get("branch_name") or "").strip()
        branch_id = None
        if branch_name:
            branch = db.query(Branch).filter(
                Branch.tenant_id == user.tenant_id, Branch.is_deleted == False,
                func.lower(Branch.name) == branch_name.lower(),
            ).first()
            if not branch:
                errors.append({"row": i, "error": f"branch_name {branch_name} not found", "data": dict(row)})
                continue
            branch_id = branch.id

        stock = db.query(ProductStock).filter(ProductStock.variant_id == variant.id).first()
        available = stock.qty_available if stock else 0

        edd = (row.get("expected_delivery_date") or "").strip() or None

        group = rows_by_phone.setdefault(phone, {
            "customer": customer, "items": [], "agent_id": None, "branch_id": None,
        })
        if agent_id:
            group["agent_id"] = agent_id
        if branch_id:
            group["branch_id"] = branch_id
        group["items"].append({
            "variant": variant, "qty": qty, "manual_price": manual_price,
            "available": available, "unavailable": available < qty,
            "expected_delivery_date": edd,
            "notes": (row.get("notes") or "").strip() or None,
        })

    preview_orders = [
        {"customer_phone": phone, "customer_name": data["customer"].name,
         "customer_id": data["customer"].id, "items": data["items"],
         "agent_id": data["agent_id"], "branch_id": data["branch_id"]}
        for phone, data in rows_by_phone.items()
    ]
    unavailable_count = sum(
        1 for o in preview_orders for it in o["items"] if it["unavailable"]
    )

    return JSONResponse({
        "order_count": len(preview_orders),
        "unavailable_count": unavailable_count,
        "orders": [
            {
                "customer_phone": o["customer_phone"],
                "customer_name": o["customer_name"],
                "customer_id": o["customer_id"],
                "agent_id": o["agent_id"],
                "branch_id": o["branch_id"],
                "items": [
                    {
                        "variant_id": it["variant"].id,
                        "product_name": f"{it['variant'].product.name} — {it['variant'].sku_code}" if it["variant"].product else it["variant"].sku_code,
                        "qty": it["qty"],
                        "manual_price": it["manual_price"],
                        "available": it["available"],
                        "unavailable": it["unavailable"],
                        "expected_delivery_date": it["expected_delivery_date"],
                        "notes": it["notes"],
                    }
                    for it in o["items"]
                ],
            }
            for o in preview_orders
        ],
        "errors": errors,
    })


@router.post("/sales/orders/bulk-upload/confirm")
def bulk_upload_confirm(
    payload: dict,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    orders_payload = payload.get("orders", [])
    created = 0

    for o in orders_payload:
        customer = db.query(Customer).filter(
            Customer.id == o.get("customer_id"), Customer.tenant_id == user.tenant_id,
            Customer.is_deleted == False,
        ).first()
        if not customer:
            continue

        agent_id = o.get("agent_id") or customer.assigned_agent_id or user.id
        branch_id = o.get("branch_id")

        edd = None
        first_edd = next((it.get("expected_delivery_date") for it in o.get("items", []) if it.get("expected_delivery_date")), None)
        if first_edd:
            try:
                edd = date.fromisoformat(first_edd)
            except ValueError:
                edd = None

        order = SalesOrder(
            display_id=generate_order_display_id(db, user.tenant_id),
            tenant_id=user.tenant_id,
            customer_id=customer.id,
            agent_id=agent_id,
            branch_id=branch_id,
            status="DRAFT",
            payment_terms=customer.default_payment_terms,
            expected_delivery_date=edd,
        )
        db.add(order)
        db.flush()

        for it in o.get("items", []):
            variant_id = it.get("variant_id")
            qty = it.get("qty")
            manual_price = it.get("manual_price")
            price = manual_price if manual_price is not None else 0.0
            price_source = "MANUAL" if manual_price is not None else "NONE"

            variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
            unit_id = None
            if variant:
                unit_id = variant.base_unit_id or (variant.product.base_unit_id if variant.product else None)
            db.add(SalesOrderItem(
                order_id=order.id,
                tenant_id=user.tenant_id,
                variant_id=variant_id,
                qty_ordered=qty,
                unit_id=unit_id,
                unit_price=price,
                price_source=price_source,
                manual_override_price=manual_price,
                line_total=qty * price,
                stock_status="UNAVAILABLE" if it.get("unavailable") else "AVAILABLE",
            ))
        created += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no orders were created. {e}")
    return JSONResponse({"created": created})

