"""
Sales Intelligence & Analytics — Brief 07.
Tier overview pages, anomaly alert feed, intelligence dashboard.
Admin/Manager only. Feature-gated by SALES_ANALYTICS.
"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException, Query, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from .database import (
    get_db, Customer, Product, ProductVariant, SalesOrder, SalesOrderItem,
    TierSnapshot, User, Category, SubCategory,
)
from .auth import get_current_user, get_current_user_or_redirect, has_module, get_user_tabs
from .constants import has_feature
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread
from .sales_orders import _sales_agents
from .sales_catalog import _active_categories, _active_subcategories

router = APIRouter()

TIER_CHOICES = ("A", "B", "C", "D", "UNRANKED")


def _require_analytics(request: Request, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> User:
    if not has_module(user, "SALES"):
        raise HTTPException(status_code=403, detail="Sales module not enabled for this user")
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Admin/Manager only")
    tenant = user.tenant
    if not has_feature(tenant, "SALES_MODULE", db):
        raise HTTPException(status_code=403, detail="Sales module not enabled for this tenant")
    if not has_feature(tenant, "SALES_ANALYTICS", db):
        raise HTTPException(status_code=403, detail="Sales Analytics not enabled for this tenant")
    if user.role == "MANAGER" and "SALES_ANALYTICS" not in get_user_tabs(user, tenant, db):
        raise HTTPException(status_code=403, detail="Sales Insights not enabled for this user")
    return user

def _require_analytics_or_redirect(request: Request, user: User = Depends(get_current_user_or_redirect),
                                    db: Session = Depends(get_db)) -> User:
    """Same checks as _require_analytics, for GET page routes: missing/invalid
    session redirects to /login instead of raw 401 JSON."""
    if not has_module(user, "SALES"):
        raise HTTPException(status_code=403, detail="Sales module not enabled for this user")
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Admin/Manager only")
    tenant = user.tenant
    if not has_feature(tenant, "SALES_MODULE", db):
        raise HTTPException(status_code=403, detail="Sales module not enabled for this tenant")
    if not has_feature(tenant, "SALES_ANALYTICS", db):
        raise HTTPException(status_code=403, detail="Sales Analytics not enabled for this tenant")
    if user.role == "MANAGER" and "SALES_ANALYTICS" not in get_user_tabs(user, tenant, db):
        raise HTTPException(status_code=403, detail="Sales Insights not enabled for this user")
    return user


def _ctx(db: Session, user: User, **extra) -> dict:
    ctx = {
        "user": user, "L": _L(db, user), "unread": _unread(db, user),
    }
    ctx.update(_nav_ctx(db, user))
    ctx.update(extra)
    return ctx


def _latest_tier_snapshots(db: Session, tenant_id: str, entity_type: str):
    latest_sq = (
        db.query(
            TierSnapshot.entity_id,
            func.max(TierSnapshot.computed_at).label("latest")
        )
        .filter(TierSnapshot.tenant_id   == tenant_id,
                TierSnapshot.entity_type == entity_type)
        .group_by(TierSnapshot.entity_id)
        .subquery()
    )
    return (
        db.query(TierSnapshot)
        .join(latest_sq, and_(
            TierSnapshot.entity_id   == latest_sq.c.entity_id,
            TierSnapshot.computed_at == latest_sq.c.latest,
        ))
        .filter(TierSnapshot.tenant_id == tenant_id, TierSnapshot.entity_type == entity_type)
        .all()
    )


# ══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

GP_STATUS_CHOICES = ("CONFIRMED", "DISPATCHED", "DELIVERED")


@router.get("/sales/analytics", response_class=HTMLResponse)
def analytics_dashboard(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    agent_id: list = Query(default=[]),
    customer_id: list = Query(default=[]),
    category_id: list = Query(default=[]),
    sub_category_id: list = Query(default=[]),
    status: list = Query(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(_require_analytics_or_redirect),
):
    """
    Sales Insights — a single, self-contained page (2026-07 redesign): no more
    hopping into separate Anomaly/Tiers/Volume sub-pages. Starts with Gross
    Profit (order/SKU/client level, tier shown only as an attribute of each
    row — not a separate tier-distribution chart). Anomaly detection has been
    removed from this surface entirely (the underlying AnomalyAlert
    generation in sales_ai.py is untouched — only this UI section is gone).

    Cost basis: this system has no per-lot/FIFO stock ledger — the only cost
    signal is ProductStock.avg_cost, a moving weighted-average recalculated on
    every STOCK_IN (see sales_inventory.py's handle_stock_in). SalesOrderItem
    snapshots that average into cost_snapshot at order confirmation. So "cost"
    below is that weighted-average cost at confirmation time, not true FIFO —
    functionally "average of the cost prices in effect," the fallback basis
    allowed for when multiple cost prices exist. Only CONFIRMED/DISPATCHED/
    DELIVERED orders carry a cost_snapshot (DRAFT isn't costed yet; CANCELLED
    is excluded as non-revenue).
    """
    tenant_id = user.tenant_id

    try:
        df = datetime.strptime(date_from, "%Y-%m-%d") if date_from else datetime.utcnow() - timedelta(days=30)
    except ValueError:
        df = datetime.utcnow() - timedelta(days=30)
    try:
        dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1) if date_to else datetime.utcnow() + timedelta(days=1)
    except ValueError:
        dt = datetime.utcnow() + timedelta(days=1)

    statuses = [s for s in status if s in GP_STATUS_CHOICES] or list(GP_STATUS_CHOICES)

    q = (
        db.query(SalesOrderItem, SalesOrder, Customer, ProductVariant, Product)
        .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
        .join(Customer, SalesOrder.customer_id == Customer.id)
        .join(ProductVariant, SalesOrderItem.variant_id == ProductVariant.id)
        .join(Product, ProductVariant.product_id == Product.id)
        .filter(
            SalesOrder.tenant_id == tenant_id,
            SalesOrder.is_deleted == False,
            SalesOrder.status.in_(statuses),
            SalesOrder.created_at >= df,
            SalesOrder.created_at < dt,
        )
    )
    if agent_id:
        q = q.filter(SalesOrder.agent_id.in_(agent_id))
    if customer_id:
        q = q.filter(SalesOrder.customer_id.in_(customer_id))
    if sub_category_id:
        q = q.filter(Product.sub_category_id.in_(sub_category_id))
    elif category_id:
        sub_ids = [sc.id for cid in category_id for sc in _active_subcategories(db, tenant_id, cid)]
        q = q.filter(Product.sub_category_id.in_(sub_ids))

    rows = q.all()

    orders, skus, clients = {}, {}, {}
    trend = {}  # date -> {revenue, cost}
    excluded_no_cost = 0

    for item, order, customer, variant, product in rows:
        revenue = item.line_total or (item.unit_price * item.qty_ordered)
        # 2026-07 FIFO redesign: a manual cost override (requirement #6) always
        # wins over the auto FIFO-weighted cost_snapshot for GP purposes.
        effective_cost_per_unit = (
            item.cost_snapshot_override if item.cost_snapshot_override is not None else item.cost_snapshot
        )
        if effective_cost_per_unit is None:
            excluded_no_cost += 1
            cost = 0.0
        else:
            cost = effective_cost_per_unit * item.qty_ordered
        gp = revenue - cost

        o = orders.setdefault(order.id, {
            "id": order.id, "display_id": order.display_id or order.id[:8], "customer": customer.name,
            "customer_tier": customer.customer_tier, "agent": order.agent.name if order.agent else "—",
            "created": order.created_at, "revenue": 0.0, "cost": 0.0,
        })
        o["revenue"] += revenue
        o["cost"] += cost

        s = skus.setdefault(variant.id, {
            "id": variant.id, "product_name": product.name, "sku_code": variant.sku_code,
            "label": variant.variant_label or variant.sku_code, "tier": variant.product_tier,
            "qty": 0.0, "revenue": 0.0, "cost": 0.0,
        })
        s["qty"] += item.qty_ordered
        s["revenue"] += revenue
        s["cost"] += cost

        c = clients.setdefault(customer.id, {
            "id": customer.id, "name": customer.name, "tier": customer.customer_tier,
            "order_ids": set(), "revenue": 0.0, "cost": 0.0,
        })
        c["order_ids"].add(order.id)
        c["revenue"] += revenue
        c["cost"] += cost

        day = order.created_at.strftime("%d %b") if order.created_at else "—"
        t = trend.setdefault(day, {"sort": order.created_at, "revenue": 0.0, "cost": 0.0})
        t["revenue"] += revenue
        t["cost"] += cost

    def _finish(d, revenue, cost):
        gp = revenue - cost
        d["revenue"] = revenue
        d["cost"] = cost
        d["gp"] = gp
        d["gp_pct"] = (gp / revenue * 100) if revenue else 0.0
        return d

    order_rows = sorted(
        [_finish(o, o["revenue"], o["cost"]) for o in orders.values()],
        key=lambda x: x["created"] or datetime.min, reverse=True,
    )
    sku_rows = sorted(
        [_finish(s, s["revenue"], s["cost"]) for s in skus.values()],
        key=lambda x: x["gp"], reverse=True,
    )
    client_rows = sorted(
        [_finish({**c, "order_count": len(c["order_ids"])}, c["revenue"], c["cost"]) for c in clients.values()],
        key=lambda x: x["gp"], reverse=True,
    )

    total_revenue = sum(o["revenue"] for o in order_rows)
    total_cost = sum(o["cost"] for o in order_rows)
    total_gp = total_revenue - total_cost
    total_gp_pct = (total_gp / total_revenue * 100) if total_revenue else 0.0

    trend_sorted = sorted(trend.items(), key=lambda kv: kv[1]["sort"] or datetime.min)
    trend_labels = [k for k, v in trend_sorted]
    trend_revenue = [round(v["revenue"], 2) for k, v in trend_sorted]
    trend_gp = [round(v["revenue"] - v["cost"], 2) for k, v in trend_sorted]

    top_sku_labels = [f"{s['product_name']} — {s['label']}" for s in sku_rows[:10]]
    top_sku_gp = [round(s["gp"], 2) for s in sku_rows[:10]]
    top_client_labels = [c["name"] for c in client_rows[:10]]
    top_client_gp = [round(c["gp"], 2) for c in client_rows[:10]]

    return templates.TemplateResponse("sales/analytics_dashboard.html", _ctx(
        db, user, request=request,
        date_from=df.strftime("%Y-%m-%d"), date_to=(dt - timedelta(days=1)).strftime("%Y-%m-%d"),
        agent_id=agent_id, customer_id=customer_id, category_id=category_id, sub_category_id=sub_category_id,
        status=statuses, status_choices=GP_STATUS_CHOICES,
        agents=_sales_agents(db, tenant_id),
        customers=db.query(Customer).filter(Customer.tenant_id == tenant_id, Customer.is_deleted == False).order_by(Customer.name).all(),
        categories=_active_categories(db, tenant_id), subcategories=_active_subcategories(db, tenant_id),
        order_rows=order_rows, sku_rows=sku_rows, client_rows=client_rows,
        total_revenue=total_revenue, total_cost=total_cost, total_gp=total_gp, total_gp_pct=total_gp_pct,
        excluded_no_cost=excluded_no_cost,
        trend_labels=trend_labels, trend_revenue=trend_revenue, trend_gp=trend_gp,
        top_sku_labels=top_sku_labels, top_sku_gp=top_sku_gp,
        top_client_labels=top_client_labels, top_client_gp=top_client_gp,
    ))


# Anomaly Detection UI removed (2026-07 redesign) — this surface (route +
# template) used to live at /sales/analytics/anomalies. The underlying
# AnomalyAlert generation in sales_ai.py is untouched; only this page is gone.


# ══════════════════════════════════════════════════════════════════════════════
# FIFO COST BREAKDOWN + MANUAL OVERRIDE (2026-07)
# ══════════════════════════════════════════════════════════════════════════════

def _gp_filtered_items(db, tenant_id, level, entity_id, date_from, date_to, agent_id, customer_id, category_id, sub_category_id, status):
    """Same filter logic as analytics_dashboard(), narrowed to one order /
    variant / customer for the "how was this cost calculated?" breakdown."""
    try:
        df = datetime.strptime(date_from, "%Y-%m-%d") if date_from else datetime.utcnow() - timedelta(days=30)
    except ValueError:
        df = datetime.utcnow() - timedelta(days=30)
    try:
        dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1) if date_to else datetime.utcnow() + timedelta(days=1)
    except ValueError:
        dt = datetime.utcnow() + timedelta(days=1)
    statuses = [s for s in status if s in GP_STATUS_CHOICES] or list(GP_STATUS_CHOICES)

    q = (
        db.query(SalesOrderItem, SalesOrder, Customer, ProductVariant, Product)
        .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
        .join(Customer, SalesOrder.customer_id == Customer.id)
        .join(ProductVariant, SalesOrderItem.variant_id == ProductVariant.id)
        .join(Product, ProductVariant.product_id == Product.id)
        .filter(
            SalesOrder.tenant_id == tenant_id, SalesOrder.is_deleted == False,
            SalesOrder.status.in_(statuses), SalesOrder.created_at >= df, SalesOrder.created_at < dt,
        )
    )
    if agent_id:
        q = q.filter(SalesOrder.agent_id.in_(agent_id))
    if customer_id:
        q = q.filter(SalesOrder.customer_id.in_(customer_id))
    if sub_category_id:
        q = q.filter(Product.sub_category_id.in_(sub_category_id))
    elif category_id:
        sub_ids = [sc.id for cid in category_id for sc in _active_subcategories(db, tenant_id, cid)]
        q = q.filter(Product.sub_category_id.in_(sub_ids))

    if level == "order":
        q = q.filter(SalesOrder.id == entity_id)
    elif level == "sku":
        q = q.filter(ProductVariant.id == entity_id)
    elif level == "client":
        q = q.filter(Customer.id == entity_id)

    return [row[0] for row in q.all()]  # SalesOrderItem rows


@router.get("/sales/analytics/api/fifo-breakdown")
def fifo_breakdown_api(
    level: str, id: str,
    date_from: str = "", date_to: str = "",
    agent_id: list = Query(default=[]), customer_id: list = Query(default=[]),
    category_id: list = Query(default=[]), sub_category_id: list = Query(default=[]),
    status: list = Query(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(_require_analytics),
):
    """Backs the "🔍 How was this cost calculated?" modal — for order/SKU/
    client level, lists the underlying order line items (each editable via
    /sales/analytics/api/override-cost) plus the aggregated FIFO lot
    breakdown (which PO(s), how much, at what cost) that produced the
    auto-computed weighted-average cost."""
    if level not in ("order", "sku", "client"):
        raise HTTPException(400, "level must be order, sku, or client")

    items = _gp_filtered_items(
        db, user.tenant_id, level, id, date_from, date_to,
        agent_id, customer_id, category_id, sub_category_id, status,
    )

    item_rows = []
    lot_totals = {}  # (po_display_id, is_fallback) -> {qty, cost_total}
    for item in items:
        effective = item.cost_snapshot_override if item.cost_snapshot_override is not None else item.cost_snapshot
        item_rows.append({
            "order_item_id": item.id,
            "order_display_id": item.order.display_id or item.order.id[:8],
            "product_name": item.variant.product.name if item.variant and item.variant.product else "—",
            "variant_label": item.variant.variant_label or item.variant.sku_code if item.variant else "—",
            "qty": item.qty_ordered,
            "auto_cost": item.cost_snapshot,
            "override_cost": item.cost_snapshot_override,
            "override_note": item.cost_override_note,
            "effective_cost": effective,
        })

    from .database import FifoConsumption, StockLot, InventoryPurchaseOrder
    consumptions = db.query(FifoConsumption, StockLot, InventoryPurchaseOrder).filter(
        FifoConsumption.order_item_id.in_([i.id for i in items]),
    ).outerjoin(StockLot, FifoConsumption.lot_id == StockLot.id).outerjoin(
        InventoryPurchaseOrder, StockLot.po_id == InventoryPurchaseOrder.id,
    ).all()

    lots = []
    for consumption, lot, po in consumptions:
        lots.append({
            "po_display_id": po.display_id if po else None,
            "received_at": lot.received_at.strftime("%d %b %Y") if lot and lot.received_at else None,
            "qty": consumption.qty,
            "unit_cost": consumption.unit_cost,
            "is_fallback": consumption.is_fallback,
        })
    # Aggregate identical (po, cost) rows so a PO split across many order
    # items shows as one line rather than N near-duplicate lines.
    agg = {}
    for l in lots:
        key = (l["po_display_id"], l["unit_cost"], l["is_fallback"])
        a = agg.setdefault(key, {**l, "qty": 0.0})
        a["qty"] += l["qty"]
    lots_agg = sorted(agg.values(), key=lambda x: (x["is_fallback"], x["received_at"] or ""))

    total_qty = sum(l["qty"] for l in lots_agg)
    total_cost = sum(l["qty"] * l["unit_cost"] for l in lots_agg)
    weighted_avg = (total_cost / total_qty) if total_qty else 0.0

    return JSONResponse({
        "items": item_rows,
        "lots": lots_agg,
        "weighted_avg_cost": weighted_avg,
    })


@router.post("/sales/analytics/api/override-cost")
def override_cost_api(
    order_item_id: str = Form(...),
    cost: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(_require_analytics),
):
    """Requirement #6 — let a user correct the auto-filled FIFO cost for GP
    reporting. Editing here never touches actual stock lots/balances (those
    reflect what physically happened); it only changes which cost Sales
    Insights uses when computing GP for this line item. Pass an empty
    `cost` to clear the override and revert to the auto FIFO value."""
    item = db.query(SalesOrderItem).join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id).filter(
        SalesOrderItem.id == order_item_id, SalesOrder.tenant_id == user.tenant_id,
    ).first()
    if not item:
        raise HTTPException(404, "Order item not found")

    if cost.strip() == "":
        item.cost_snapshot_override = None
        item.cost_override_note = None
        item.cost_override_by_id = None
        item.cost_override_at = None
    else:
        try:
            item.cost_snapshot_override = float(cost)
        except ValueError:
            return JSONResponse({"error": "Cost must be a number"}, status_code=400)
        item.cost_override_note = note.strip() or None
        item.cost_override_by_id = user.id
        item.cost_override_at = datetime.utcnow()

    db.commit()
    return JSONResponse({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
# TIER PAGES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/analytics/tiers/customers", response_class=HTMLResponse)
def customer_tiers(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(_require_analytics_or_redirect),
                    tier: str = None, agent_id: str = None):
    tenant_id = user.tenant_id
    snapshots = _latest_tier_snapshots(db, tenant_id, "CUSTOMER")
    snap_by_customer = {s.entity_id: s for s in snapshots}

    q = db.query(Customer).filter(Customer.tenant_id == tenant_id, Customer.is_deleted == False)
    if tier:
        q = q.filter(Customer.customer_tier == tier)
    if agent_id:
        q = q.filter(Customer.assigned_agent_id == agent_id)
    customers = q.order_by(Customer.name).all()

    rows = []
    for c in customers:
        snap = snap_by_customer.get(c.id)
        basis = json.loads(snap.basis_json) if snap and snap.basis_json else None
        rows.append({"customer": c, "snapshot": snap, "basis": basis})

    agents = db.query(User).filter(
        User.tenant_id == tenant_id, User.is_deleted == False, User.role == "EMPLOYEE",
    ).order_by(User.name).all()

    return templates.TemplateResponse("sales/analytics_tiers_customers.html", _ctx(
        db, user, request=request, rows=rows, agents=agents,
        tier_choices=TIER_CHOICES, filter_tier=tier, filter_agent_id=agent_id,
    ))


@router.get("/sales/analytics/tiers/products", response_class=HTMLResponse)
def product_tiers(request: Request, db: Session = Depends(get_db),
                   user: User = Depends(_require_analytics_or_redirect), tier: str = None):
    tenant_id = user.tenant_id
    snapshots = _latest_tier_snapshots(db, tenant_id, "PRODUCT")
    snap_by_variant = {s.entity_id: s for s in snapshots}

    q = db.query(ProductVariant).join(Product, ProductVariant.product_id == Product.id).filter(
        ProductVariant.tenant_id == tenant_id, ProductVariant.is_deleted == False,
    )
    if tier:
        q = q.filter(ProductVariant.product_tier == tier)
    variants = q.order_by(Product.name, ProductVariant.sku_code).all()

    rows = []
    for v in variants:
        snap = snap_by_variant.get(v.id)
        basis = json.loads(snap.basis_json) if snap and snap.basis_json else None
        rows.append({"product": v, "snapshot": snap, "basis": basis})

    return templates.TemplateResponse("sales/analytics_tiers_products.html", _ctx(
        db, user, request=request, rows=rows,
        tier_choices=TIER_CHOICES, filter_tier=tier,
    ))


# ══════════════════════════════════════════════════════════════════════════════
# VOLUME / REVENUE BREAKDOWN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/analytics/volume", response_class=HTMLResponse)
def volume_breakdown(request: Request, db: Session = Depends(get_db),
                      user: User = Depends(_require_analytics_or_redirect)):
    tenant_id = user.tenant_id
    cutoff = datetime.utcnow() - timedelta(days=90)

    rows = (
        db.query(
            ProductVariant.id, Product.name, ProductVariant.sku_code, ProductVariant.product_tier,
            func.sum(SalesOrderItem.line_total).label("revenue"),
            func.sum(SalesOrderItem.qty_ordered).label("volume"),
        )
        .join(Product, ProductVariant.product_id == Product.id)
        .join(SalesOrderItem, SalesOrderItem.variant_id == ProductVariant.id)
        .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
        .filter(
            SalesOrder.tenant_id  == tenant_id,
            SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
            SalesOrder.created_at >= cutoff,
            SalesOrder.is_deleted == False,
        )
        .group_by(ProductVariant.id, Product.name, ProductVariant.sku_code, ProductVariant.product_tier)
        .order_by(func.sum(SalesOrderItem.line_total).desc())
        .all()
    )

    return templates.TemplateResponse("sales/analytics_volume.html", _ctx(
        db, user, request=request, rows=rows,
    ))


