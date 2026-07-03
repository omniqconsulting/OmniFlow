"""
Sales Intelligence & Analytics — Brief 07.
Tier overview pages, anomaly alert feed, intelligence dashboard.
Admin/Manager only. Feature-gated by SALES_ANALYTICS.
"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from .database import (
    get_db, Customer, Product, ProductVariant, SalesOrder, SalesOrderItem,
    TierSnapshot, AnomalyAlert, User,
)
from .auth import get_current_user, has_module
from .constants import has_feature
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread

router = APIRouter()

TIER_CHOICES = ("A", "B", "C", "D", "UNRANKED")
SEVERITY_CHOICES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
ALERT_TYPES = (
    "PRICE_SPIKE", "MARGIN_DROP", "CUSTOMER_DROPOUT",
    "LOW_STOCK", "AGENT_NEGLECT", "ORDER_CANCEL_SPIKE",
)


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

@router.get("/sales/analytics", response_class=HTMLResponse)
def analytics_dashboard(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(_require_analytics)):
    tenant_id = user.tenant_id
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_end = month_start - timedelta(seconds=1)
    prev_month_start = prev_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    thirty_days_ago = now - timedelta(days=30)

    def revenue_in(start, end):
        return db.query(func.sum(SalesOrder.total_amount)).filter(
            SalesOrder.tenant_id  == tenant_id,
            SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
            SalesOrder.created_at >= start,
            SalesOrder.created_at <  end,
            SalesOrder.is_deleted == False,
        ).scalar() or 0

    this_month_revenue = revenue_in(month_start, now)
    last_month_revenue = revenue_in(prev_month_start, month_start)
    revenue_change_pct = (
        round((this_month_revenue - last_month_revenue) / last_month_revenue * 100, 1)
        if last_month_revenue else None
    )

    margin_row = db.query(
        func.avg(
            (SalesOrderItem.unit_price - SalesOrderItem.cost_snapshot)
            / SalesOrderItem.unit_price * 100
        )
    ).join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id).filter(
        SalesOrder.tenant_id   == tenant_id,
        SalesOrderItem.cost_snapshot != None,
        SalesOrderItem.unit_price    > 0,
        SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
        SalesOrder.created_at >= month_start,
        SalesOrder.is_deleted == False,
    ).scalar()
    gross_margin_pct = round(margin_row, 1) if margin_row else None

    active_tier_a_customers = db.query(func.count(Customer.id)).filter(
        Customer.tenant_id    == tenant_id,
        Customer.customer_tier == "A",
        Customer.is_deleted   == False,
    ).scalar() or 0

    unread_alert_count = db.query(func.count(AnomalyAlert.id)).filter(
        AnomalyAlert.tenant_id    == tenant_id,
        AnomalyAlert.is_read      == False,
        AnomalyAlert.is_dismissed == False,
    ).scalar() or 0

    recent_alerts = (
        db.query(AnomalyAlert)
        .filter(AnomalyAlert.tenant_id == tenant_id, AnomalyAlert.is_dismissed == False)
        .order_by(AnomalyAlert.is_read.asc(), AnomalyAlert.detected_at.desc())
        .limit(5)
        .all()
    )

    def tier_distribution(entity_type, model, tier_col):
        counts = {t: 0 for t in TIER_CHOICES}
        rows = db.query(tier_col, func.count(model.id)).filter(
            model.tenant_id == tenant_id, model.is_deleted == False,
        ).group_by(tier_col).all()
        for tier, cnt in rows:
            counts[tier or "UNRANKED"] = cnt
        return counts

    customer_tier_dist = tier_distribution("CUSTOMER", Customer, Customer.customer_tier)
    product_tier_dist  = tier_distribution("PRODUCT",  ProductVariant,  ProductVariant.product_tier)

    top_products = (
        db.query(ProductVariant.id, Product.name, func.sum(SalesOrderItem.line_total).label("revenue"))
        .join(Product, ProductVariant.product_id == Product.id)
        .join(SalesOrderItem, SalesOrderItem.variant_id == ProductVariant.id)
        .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
        .filter(
            SalesOrder.tenant_id  == tenant_id,
            SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
            SalesOrder.created_at >= thirty_days_ago,
            SalesOrder.is_deleted == False,
        )
        .group_by(ProductVariant.id, Product.name)
        .order_by(func.sum(SalesOrderItem.line_total).desc())
        .limit(5)
        .all()
    )

    top_customers = (
        db.query(Customer.id, Customer.name, func.sum(SalesOrder.total_amount).label("value"))
        .join(SalesOrder, SalesOrder.customer_id == Customer.id)
        .filter(
            SalesOrder.tenant_id  == tenant_id,
            SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
            SalesOrder.created_at >= thirty_days_ago,
            SalesOrder.is_deleted == False,
        )
        .group_by(Customer.id, Customer.name)
        .order_by(func.sum(SalesOrder.total_amount).desc())
        .limit(5)
        .all()
    )

    return templates.TemplateResponse("sales/analytics_dashboard.html", _ctx(
        db, user, request=request,
        this_month_revenue=this_month_revenue, last_month_revenue=last_month_revenue,
        revenue_change_pct=revenue_change_pct, gross_margin_pct=gross_margin_pct,
        active_tier_a_customers=active_tier_a_customers, unread_alert_count=unread_alert_count,
        recent_alerts=recent_alerts,
        customer_tier_dist=customer_tier_dist, product_tier_dist=product_tier_dist,
        top_products=top_products, top_customers=top_customers,
    ))


# ══════════════════════════════════════════════════════════════════════════════
# ANOMALY ALERT FEED
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/analytics/anomalies", response_class=HTMLResponse)
def anomaly_feed(request: Request, db: Session = Depends(get_db),
                  user: User = Depends(_require_analytics),
                  alert_type: str = None, severity: str = None,
                  is_read: str = None, show_dismissed: str = None):
    tenant_id = user.tenant_id
    q = db.query(AnomalyAlert).filter(AnomalyAlert.tenant_id == tenant_id)
    if show_dismissed != "1":
        q = q.filter(AnomalyAlert.is_dismissed == False)
    if alert_type:
        q = q.filter(AnomalyAlert.alert_type == alert_type)
    if severity:
        q = q.filter(AnomalyAlert.severity == severity)
    if is_read == "1":
        q = q.filter(AnomalyAlert.is_read == True)
    elif is_read == "0":
        q = q.filter(AnomalyAlert.is_read == False)
    alerts = q.order_by(AnomalyAlert.detected_at.desc()).all()

    return templates.TemplateResponse("sales/analytics_anomalies.html", _ctx(
        db, user, request=request, alerts=alerts,
        alert_types=ALERT_TYPES, severities=SEVERITY_CHOICES,
        filter_alert_type=alert_type, filter_severity=severity,
        filter_is_read=is_read, filter_show_dismissed=show_dismissed,
    ))


def _get_alert_or_404(db: Session, alert_id: str, tenant_id: str) -> AnomalyAlert:
    a = db.query(AnomalyAlert).filter(
        AnomalyAlert.id == alert_id, AnomalyAlert.tenant_id == tenant_id,
    ).first()
    if not a:
        raise HTTPException(404, "Alert not found")
    return a


@router.post("/sales/analytics/anomalies/{alert_id}/read")
def mark_alert_read(alert_id: str, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(_require_analytics)):
    alert = _get_alert_or_404(db, alert_id, user.tenant_id)
    alert.is_read = True
    db.commit()
    return RedirectResponse(request.headers.get("referer", "/sales/analytics/anomalies"), status_code=303)


@router.post("/sales/analytics/anomalies/{alert_id}/dismiss")
def dismiss_alert(alert_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(_require_analytics)):
    alert = _get_alert_or_404(db, alert_id, user.tenant_id)
    alert.is_dismissed = True
    db.commit()
    return RedirectResponse(request.headers.get("referer", "/sales/analytics/anomalies"), status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# TIER PAGES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/analytics/tiers/customers", response_class=HTMLResponse)
def customer_tiers(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(_require_analytics),
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
                   user: User = Depends(_require_analytics), tier: str = None):
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
                      user: User = Depends(_require_analytics)):
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
