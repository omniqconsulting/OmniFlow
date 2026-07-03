"""
Sales AI Intelligence — Brief 07.
Tier classification (weekly) and anomaly detection (daily) for the Sales module.
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import func, case

from .database import (
    TierSnapshot, AnomalyAlert,
    Product, ProductVariant, Customer, SalesOrder, SalesOrderItem, CostEntry, ProductStock,
    CRMCallLog, User,
)
from .auth import has_module


def run_tier_classification(db, tenant_id: str):
    """
    Computes A/B/C/D tiers for all variants (SKUs) and customers.
    Reads last 90 days of confirmed/dispatched/delivered orders.
    Writes TierSnapshot rows and updates product_variants.product_tier + customers.customer_tier.
    Safe to run multiple times — existing tier values are overwritten.
    """
    period_label = "W" + datetime.utcnow().strftime("%Y-%U")
    cutoff       = datetime.utcnow() - timedelta(days=90)

    # ── VARIANT TIERS: Pareto by revenue contribution ──────────────────────
    variant_stats = (
        db.query(
            SalesOrderItem.variant_id,
            func.sum(SalesOrderItem.line_total).label("revenue"),
            func.sum(SalesOrderItem.qty_ordered).label("volume"),
            func.avg(
                case(
                    (SalesOrderItem.cost_snapshot != None,
                     (SalesOrderItem.unit_price - SalesOrderItem.cost_snapshot)
                     / SalesOrderItem.unit_price * 100),
                    else_=None,
                )
            ).label("avg_margin_pct"),
        )
        .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
        .filter(
            SalesOrder.tenant_id  == tenant_id,
            SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
            SalesOrder.created_at >= cutoff,
            SalesOrder.is_deleted == False,
        )
        .group_by(SalesOrderItem.variant_id)
        .order_by(func.sum(SalesOrderItem.line_total).desc())
        .all()
    )

    total_revenue = sum(r.revenue or 0 for r in variant_stats) or 1
    cumulative = 0

    for row in variant_stats:
        cumulative += (row.revenue or 0)
        pct = cumulative / total_revenue * 100
        tier = "A" if pct <= 70 else ("B" if pct <= 90 else ("C" if pct <= 98 else "D"))

        db.add(TierSnapshot(
            tenant_id    = tenant_id,
            entity_type  = "PRODUCT",
            entity_id    = row.variant_id,
            tier         = tier,
            score        = round(row.revenue or 0, 2),
            basis_json   = json.dumps({
                "revenue_90d":            round(row.revenue or 0, 2),
                "volume_90d":             round(row.volume or 0, 2),
                "avg_margin_pct":         round(row.avg_margin_pct or 0, 1),
                "cumulative_revenue_pct": round(cumulative / total_revenue * 100, 1),
            }),
            period_label = period_label,
        ))
        db.query(ProductVariant).filter(ProductVariant.id == row.variant_id).update(
            {"product_tier": tier}
        )

    # Variants with zero orders in 90 days → UNRANKED
    sold_variant_ids = {r.variant_id for r in variant_stats}
    q = db.query(ProductVariant).filter(
        ProductVariant.tenant_id == tenant_id,
        ProductVariant.is_deleted == False,
    )
    if sold_variant_ids:
        q = q.filter(ProductVariant.id.notin_(sold_variant_ids))
    q.update({"product_tier": "UNRANKED"}, synchronize_session=False)

    # ── CUSTOMER TIERS: RFM scoring ───────────────────────────────────────
    customer_stats = (
        db.query(
            SalesOrder.customer_id,
            func.max(SalesOrder.created_at).label("last_order_at"),
            func.count(SalesOrder.id).label("order_count"),
            func.sum(SalesOrder.total_amount).label("total_value"),
        )
        .filter(
            SalesOrder.tenant_id  == tenant_id,
            SalesOrder.status.in_(["CONFIRMED", "DISPATCHED", "DELIVERED"]),
            SalesOrder.created_at >= cutoff,
            SalesOrder.is_deleted == False,
        )
        .group_by(SalesOrder.customer_id)
        .all()
    )

    if customer_stats:
        max_val  = max(r.total_value  or 0 for r in customer_stats) or 1
        max_freq = max(r.order_count  or 0 for r in customer_stats) or 1

        for row in customer_stats:
            recency_days = (datetime.utcnow() - row.last_order_at).days
            r_score = 3 if recency_days <= 30 else (2 if recency_days <= 60 else 1)
            f_score = 3 if (row.order_count / max_freq) >= 0.66 else (
                      2 if (row.order_count / max_freq) >= 0.33 else 1)
            m_score = 3 if ((row.total_value or 0) / max_val) >= 0.66 else (
                      2 if ((row.total_value or 0) / max_val) >= 0.33 else 1)
            rfm  = r_score + f_score + m_score
            tier = "A" if rfm >= 8 else ("B" if rfm >= 6 else ("C" if rfm >= 4 else "D"))

            db.add(TierSnapshot(
                tenant_id    = tenant_id,
                entity_type  = "CUSTOMER",
                entity_id    = row.customer_id,
                tier         = tier,
                score        = float(rfm),
                basis_json   = json.dumps({
                    "recency_days": recency_days,  "r_score": r_score,
                    "order_count":  row.order_count, "f_score": f_score,
                    "total_value":  round(row.total_value or 0, 2), "m_score": m_score,
                    "rfm_total":    rfm,
                }),
                period_label = period_label,
            ))
            db.query(Customer).filter(Customer.id == row.customer_id).update(
                {"customer_tier": tier}
            )

        # Customers with no orders in 90 days → UNRANKED
        ordered_customer_ids = {r.customer_id for r in customer_stats}
        q2 = db.query(Customer).filter(
            Customer.tenant_id == tenant_id,
            Customer.is_deleted == False,
        )
        if ordered_customer_ids:
            q2 = q2.filter(Customer.id.notin_(ordered_customer_ids))
        q2.update({"customer_tier": "UNRANKED"}, synchronize_session=False)

    db.commit()


def run_anomaly_detection(db, tenant_id: str, anthropic_client):
    """
    Runs deterministic SQL checks for 6 anomaly types.
    Deduplicates against recent active alerts.
    Calls Claude API once per tenant to narrate all new anomalies.
    Writes AnomalyAlert rows.
    """
    alerts_to_create = []

    # ── 1. PRICE_SPIKE: buy price up >15% vs prior readings ───────────────
    variants = db.query(ProductVariant).filter(
        ProductVariant.tenant_id == tenant_id,
        ProductVariant.is_deleted == False,
    ).all()

    def _variant_label(v):
        return f"{v.product.name} — {v.sku_code}" if v.product else v.sku_code

    for variant in variants:
        recent = (
            db.query(CostEntry)
            .filter(CostEntry.variant_id == variant.id,
                    CostEntry.cost_type  == "BUY_PRICE",
                    CostEntry.tenant_id  == tenant_id)
            .order_by(CostEntry.effective_date.desc())
            .limit(10)
            .all()
        )
        if len(recent) >= 2:
            latest   = recent[0].amount
            baseline = sum(c.amount for c in recent[1:]) / len(recent[1:])
            pct      = (latest - baseline) / baseline * 100 if baseline else 0
            if pct > 15:
                alerts_to_create.append({
                    "alert_type":   "PRICE_SPIKE",
                    "entity_type":  "PRODUCT",
                    "entity_id":    variant.id,
                    "entity_label": _variant_label(variant),
                    "severity":     "HIGH" if pct > 25 else "MEDIUM",
                    "metric":       {"current": latest, "baseline": round(baseline, 2),
                                     "pct_change": round(pct, 1)},
                })

    # ── 2. MARGIN_DROP: margin down >10 pts vs 4-week avg ─────────────────
    week_ago   = datetime.utcnow() - timedelta(days=7)
    month_ago  = datetime.utcnow() - timedelta(days=35)

    def get_avg_margin(db, tenant_id, variant_id, start, end):
        return (
            db.query(
                func.avg(
                    (SalesOrderItem.unit_price - SalesOrderItem.cost_snapshot)
                    / SalesOrderItem.unit_price * 100
                ).label("margin")
            )
            .join(SalesOrder, SalesOrderItem.order_id == SalesOrder.id)
            .filter(SalesOrder.tenant_id   == tenant_id,
                    SalesOrderItem.variant_id == variant_id,
                    SalesOrderItem.cost_snapshot != None,
                    SalesOrderItem.unit_price    > 0,
                    SalesOrder.status.in_(["CONFIRMED","DISPATCHED","DELIVERED"]),
                    SalesOrder.created_at >= start,
                    SalesOrder.created_at <  end)
            .scalar()
        )

    for variant in variants:
        this_week_margin   = get_avg_margin(db, tenant_id, variant.id, week_ago, datetime.utcnow())
        prior_month_margin = get_avg_margin(db, tenant_id, variant.id, month_ago, week_ago)
        if this_week_margin and prior_month_margin:
            drop = prior_month_margin - this_week_margin
            if drop > 10:
                alerts_to_create.append({
                    "alert_type":   "MARGIN_DROP",
                    "entity_type":  "PRODUCT",
                    "entity_id":    variant.id,
                    "entity_label": _variant_label(variant),
                    "severity":     "HIGH" if drop > 20 else "MEDIUM",
                    "metric":       {"this_week_margin":  round(this_week_margin, 1),
                                     "prior_month_margin": round(prior_month_margin, 1),
                                     "drop_pts":           round(drop, 1)},
                })

    # ── 3. CUSTOMER_DROPOUT: tier-A/B with no orders in 45 days ──────────
    cutoff_45 = datetime.utcnow() - timedelta(days=45)
    active_customers = db.query(Customer).filter(
        Customer.tenant_id     == tenant_id,
        Customer.is_deleted    == False,
        Customer.customer_tier.in_(["A", "B"]),
    ).all()

    for cust in active_customers:
        last_order_dt = (
            db.query(func.max(SalesOrder.created_at))
            .filter(SalesOrder.customer_id == cust.id,
                    SalesOrder.status.notin_(["CANCELLED"]),
                    SalesOrder.is_deleted == False)
            .scalar()
        )
        if last_order_dt and last_order_dt < cutoff_45:
            days_gone = (datetime.utcnow() - last_order_dt).days
            alerts_to_create.append({
                "alert_type":   "CUSTOMER_DROPOUT",
                "entity_type":  "CUSTOMER",
                "entity_id":    cust.id,
                "entity_label": cust.name,
                "severity":     "HIGH" if cust.customer_tier == "A" else "MEDIUM",
                "metric":       {"days_since_order": days_gone, "tier": cust.customer_tier},
            })

    # ── 4. LOW_STOCK: tier-A variant below threshold ───────────────────────
    low_stock_rows = (
        db.query(ProductStock, ProductVariant)
        .join(ProductVariant, ProductStock.variant_id == ProductVariant.id)
        .filter(
            ProductStock.tenant_id     == tenant_id,
            ProductVariant.product_tier       == "A",
            ProductVariant.low_stock_threshold != None,
            ProductStock.qty_available < ProductVariant.low_stock_threshold,
            ProductVariant.is_deleted         == False,
        )
        .all()
    )
    for stock, variant in low_stock_rows:
        alerts_to_create.append({
            "alert_type":   "LOW_STOCK",
            "entity_type":  "PRODUCT",
            "entity_id":    variant.id,
            "entity_label": _variant_label(variant),
            "severity":     "HIGH" if stock.qty_available <= 0 else "MEDIUM",
            "metric":       {"available":  stock.qty_available,
                             "threshold":  variant.low_stock_threshold,
                             "in_transit": stock.qty_in_transit},
        })

    # ── 5. AGENT_NEGLECT: agent with no call logs in 7 days ──────────────
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    agents = [u for u in db.query(User).filter(
        User.tenant_id  == tenant_id,
        User.is_active  == True,
        User.is_deleted == False,
        User.role       == "EMPLOYEE",
    ).all() if has_module(u, "SALES")]

    for agent in agents:
        log_count = db.query(func.count(CRMCallLog.id)).filter(
            CRMCallLog.tenant_id   == tenant_id,
            CRMCallLog.agent_id    == agent.id,
            CRMCallLog.created_at  >= seven_days_ago,
        ).scalar()
        if log_count == 0:
            customer_count = db.query(func.count(Customer.id)).filter(
                Customer.tenant_id         == tenant_id,
                Customer.assigned_agent_id == agent.id,
                Customer.is_deleted        == False,
            ).scalar()
            if customer_count > 0:  # Only flag if agent actually has customers
                alerts_to_create.append({
                    "alert_type":   "AGENT_NEGLECT",
                    "entity_type":  "AGENT",
                    "entity_id":    agent.id,
                    "entity_label": agent.name,
                    "severity":     "MEDIUM",
                    "metric":       {"days_no_activity": 7, "customer_count": customer_count},
                })

    # ── 6. ORDER_CANCEL_SPIKE: cancellations this week > 2x prior ────────
    this_week_start = datetime.utcnow() - timedelta(days=7)
    prev_week_start = datetime.utcnow() - timedelta(days=14)

    def count_cancellations(db, tenant_id, start, end):
        return db.query(func.count(SalesOrder.id)).filter(
            SalesOrder.tenant_id  == tenant_id,
            SalesOrder.status     == "CANCELLED",
            SalesOrder.cancelled_at >= start,
            SalesOrder.cancelled_at <  end,
        ).scalar() or 0

    this_week_cancels = count_cancellations(
        db, tenant_id, this_week_start, datetime.utcnow())
    prev_week_cancels = count_cancellations(
        db, tenant_id, prev_week_start, this_week_start)

    if this_week_cancels > 0 and prev_week_cancels > 0:
        if this_week_cancels > prev_week_cancels * 2:
            alerts_to_create.append({
                "alert_type":   "ORDER_CANCEL_SPIKE",
                "entity_type":  "ORDER",
                "entity_id":    tenant_id,
                "entity_label": "All orders",
                "severity":     "HIGH",
                "metric":       {"this_week": this_week_cancels, "prev_week": prev_week_cancels,
                                 "ratio": round(this_week_cancels / prev_week_cancels, 1)},
            })

    if not alerts_to_create:
        return

    # ── Deduplicate: skip if same alert_type+entity_id active in past 3 days
    three_days_ago = datetime.utcnow() - timedelta(days=3)
    existing_keys = {
        (a.alert_type, a.entity_id)
        for a in db.query(AnomalyAlert).filter(
            AnomalyAlert.tenant_id    == tenant_id,
            AnomalyAlert.is_dismissed == False,
            AnomalyAlert.detected_at  >= three_days_ago,
        ).all()
    }
    new_alerts = [a for a in alerts_to_create
                  if (a["alert_type"], a["entity_id"]) not in existing_keys]

    if not new_alerts:
        return

    # ── Single Claude API call: narrate all new anomalies ─────────────────
    metrics_text = "\n".join(
        f"- {a['alert_type']} on {a['entity_label']}: {json.dumps(a['metric'])}"
        for a in new_alerts
    )

    narration_map = {}
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": (
                    "You are a business analyst assistant for a trading company. "
                    "The following business anomalies have been detected. "
                    "For each, write ONE plain-English sentence (under 25 words) "
                    "that explains what happened and why a manager should care. "
                    "Return ONLY a valid JSON array. Each item must have exactly "
                    "two keys: 'alert_type' and 'entity_id' (copy from input) "
                    "and 'detail' (your sentence). No markdown, no preamble, no extra keys.\n\n"
                    f"Anomalies:\n{metrics_text}"
                ),
            }],
        )
        narrations = json.loads(response.content[0].text)
        narration_map = {
            (n["alert_type"], n["entity_id"]): n["detail"]
            for n in narrations
        }
    except Exception as e:
        # Fallback: write alerts with generic detail, don't fail silently
        print(f"[sales_ai] Claude narration failed: {e}")

    # ── Write AnomalyAlert rows ───────────────────────────────────────────
    for alert in new_alerts:
        key = (alert["alert_type"], alert["entity_id"])
        db.add(AnomalyAlert(
            tenant_id    = tenant_id,
            alert_type   = alert["alert_type"],
            entity_type  = alert["entity_type"],
            entity_id    = alert["entity_id"],
            entity_label = alert["entity_label"],
            severity     = alert["severity"],
            detail       = narration_map.get(key, f"Anomaly detected: {alert['alert_type']}"),
            metric_json  = json.dumps(alert["metric"]),
        ))

    db.commit()
