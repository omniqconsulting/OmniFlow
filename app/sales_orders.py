"""
Sales Orders — Brief 05.
Order CRUD (header + line items), stock reservation engine wiring,
dispatch/delivery flow, bulk order creation, CSV export.
"""
import csv
import io
from datetime import datetime, date
from datetime import date as _date

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import func, or_ as _or
from sqlalchemy.orm import Session
from typing import Optional

from .database import (
    get_db, new_id, User, Customer, Product, UnitOfMeasure, ProductStock,
    InventoryPurchaseOrder, InventoryPOItem, SalesOrder, SalesOrderItem,
    PriceList, PriceListItem, CustomerPriceOverride,
)
from .auth import get_current_user, has_module, require_module
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread
from .sales_inventory import reserve_stock_for_item, release_all_reservations, fulfill_reservation
from .constants import SALES_MARGIN_FLOOR_PCT

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


def resolve_price(db, customer_id: str, product_id: str, tenant_id: str) -> dict:
    """
    Per-line-item price resolution — Brief 06. Checks three levels in order.
    Returns {"price": float | None, "source": str}

    Resolution order:
      1. Active customer-specific override for (customer_id, product_id)
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
            CustomerPriceOverride.product_id  == product_id,
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
                PriceListItem.product_id    == product_id,
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
                    PriceListItem.product_id    == product_id,
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
    ))


@router.post("/sales/orders/create")
def order_create(
    customer_id: str = Form(...),
    call_log_id: str = Form(""),
    payment_terms: str = Form(""),
    delivery_address: str = Form(""),
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
        agent_id=user.id,
        status="DRAFT",
        payment_terms=payment_terms.strip() or None,
        delivery_address=delivery_address.strip() or customer.shipping_address,
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
              "manual_price", "expected_delivery_date", "notes"]


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

    products = db.query(Product).filter(
        Product.tenant_id == user.tenant_id, Product.is_deleted == False,
        Product.is_active == True,
    ).order_by(Product.name).all()

    return templates.TemplateResponse(request, "sales/order_detail.html", _ctx(
        db, user, order=order, products=products,
        can_edit=(order.status == "DRAFT" and _can_view_order(user, order)),
        can_dispatch=(user.role in ("ADMIN", "MANAGER") or has_module(user, "INVENTORY")),
        msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
    ))


@router.get("/sales/orders/api/resolve-price")
def order_resolve_price_api(
    customer_id: str,
    product_id: str,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    result = resolve_price(db, customer_id, product_id, user.tenant_id)
    return JSONResponse(result)


def _insufficient_stock_msg(db, tenant_id: str, product_id: str, qty: float):
    """Returns an error message string if qty exceeds available stock, else None."""
    stock = db.query(ProductStock).filter(
        ProductStock.product_id == product_id, ProductStock.tenant_id == tenant_id,
    ).first()
    available = stock.qty_available if stock else 0
    if qty <= available:
        return None

    in_transit_date = (
        db.query(func.min(InventoryPurchaseOrder.expected_arrival_date))
        .join(InventoryPOItem, InventoryPOItem.po_id == InventoryPurchaseOrder.id)
        .filter(
            InventoryPOItem.product_id == product_id,
            InventoryPurchaseOrder.tenant_id == tenant_id,
            InventoryPurchaseOrder.status.in_(["SUBMITTED", "APPROVED", "PARTIALLY_RECEIVED"]),
        )
        .scalar()
    )
    if in_transit_date:
        return f"Only+{available:g}+unit(s)+available.+Next+stock+arrives+{in_transit_date.isoformat()}"
    return f"Only+{available:g}+unit(s)+available.+No+restock+scheduled"


@router.post("/sales/orders/{order_id}/add-item")
def order_add_item(
    order_id: str,
    product_id: str = Form(...),
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

    product = db.query(Product).filter(
        Product.id == product_id, Product.tenant_id == user.tenant_id,
        Product.is_deleted == False,
    ).first()
    if not product:
        return _redir(f"/sales/orders/{order_id}?err=Invalid+product")

    try:
        qty = float(qty_ordered)
        if qty <= 0:
            raise ValueError
    except ValueError:
        return _redir(f"/sales/orders/{order_id}?err=Invalid+quantity")

    stock_err = _insufficient_stock_msg(db, user.tenant_id, product_id, qty)
    if stock_err:
        return _redir(f"/sales/orders/{order_id}?err={stock_err}")

    price_info = resolve_price(db, order.customer_id, product_id, user.tenant_id)
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

    stock = db.query(ProductStock).filter(ProductStock.product_id == product_id).first()
    cost_snapshot = stock.avg_cost if stock else None

    approval_status = None
    if price_source == "MANUAL" and not check_margin(price, cost_snapshot):
        approval_status = "PENDING"

    item = SalesOrderItem(
        order_id=order_id,
        tenant_id=user.tenant_id,
        product_id=product_id,
        qty_ordered=qty,
        unit_id=product.base_unit_id,
        unit_price=price,
        price_source=price_source,
        manual_override_price=float(manual_override_price) if manual_override_price else None,
        override_reason=override_reason.strip() or None,
        approval_status=approval_status,
        line_total=qty * price,
    )
    db.add(item)
    db.commit()
    return _redir(f"/sales/orders/{order_id}?msg=Item+added")


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

    stock_err = _insufficient_stock_msg(db, user.tenant_id, item.product_id, qty)
    if stock_err:
        return _redir(f"/sales/orders/{order_id}?err={stock_err}")

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

        stock = db.query(ProductStock).filter(ProductStock.product_id == item.product_id).first()
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
            ProductStock.product_id == item.product_id
        ).first()
        item.cost_snapshot = stock.avg_cost if stock else None

        result = reserve_stock_for_item(
            db, item.product_id, order.id, item.id,
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
                db, order.id, item.product_id,
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

@router.get("/sales/orders/api/check-stock/{product_id}")
def api_check_stock(product_id: str, user: User = Depends(_require_sales), db: Session = Depends(get_db)):
    """Returns JSON for inline stock status display on order form."""
    stock = db.query(ProductStock).filter(
        ProductStock.product_id == product_id,
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
            InventoryPOItem.product_id == product_id,
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
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))

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

        product = db.query(Product).filter(
            Product.tenant_id == user.tenant_id, Product.sku_code == sku,
            Product.is_deleted == False,
        ).first()
        if not product:
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

        stock = db.query(ProductStock).filter(ProductStock.product_id == product.id).first()
        available = stock.qty_available if stock else 0

        edd = (row.get("expected_delivery_date") or "").strip() or None

        rows_by_phone.setdefault(phone, {
            "customer": customer, "items": [],
        })["items"].append({
            "product": product, "qty": qty, "manual_price": manual_price,
            "available": available, "unavailable": available < qty,
            "expected_delivery_date": edd,
            "notes": (row.get("notes") or "").strip() or None,
        })

    preview_orders = [
        {"customer_phone": phone, "customer_name": data["customer"].name,
         "customer_id": data["customer"].id, "items": data["items"]}
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
                "items": [
                    {
                        "product_id": it["product"].id,
                        "product_name": it["product"].name,
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

        agent_id = customer.assigned_agent_id or user.id

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
            status="DRAFT",
            expected_delivery_date=edd,
        )
        db.add(order)
        db.flush()

        for it in o.get("items", []):
            product_id = it.get("product_id")
            qty = it.get("qty")
            manual_price = it.get("manual_price")
            price = manual_price if manual_price is not None else 0.0
            price_source = "MANUAL" if manual_price is not None else "NONE"

            product = db.query(Product).filter(Product.id == product_id).first()
            db.add(SalesOrderItem(
                order_id=order.id,
                tenant_id=user.tenant_id,
                product_id=product_id,
                qty_ordered=qty,
                unit_id=product.base_unit_id if product else None,
                unit_price=price,
                price_source=price_source,
                manual_override_price=manual_price,
                line_total=qty * price,
                stock_status=None,
            ))
        created += 1

    db.commit()
    return JSONResponse({"created": created})
