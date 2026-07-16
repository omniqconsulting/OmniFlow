"""
Sales CRM & Contacts — Brief 04.
Priority work queue, call logging, customer profile, bulk import/export,
follow-up reminder scheduler job.
"""
import csv
import io
import re
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import get_db, new_id, Customer, CRMCallLog, User, Tenant, SalesOrder, PriceList, SalesOrderItem, ProductVariant
from .auth import get_current_user, has_module, require_module
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread, resolve_customer_agent, _CUSTOMER_TIERS, _CUSTOMER_COLS
from .constants import BULK_IMPORT_MAX_ROWS
from .bulk_common import check_required_headers

router = APIRouter()

PAGE_SIZE = 30
TIER_CHOICES = ("A", "B", "C", "UNRANKED")
OUTCOME_CHOICES = ("CONNECTED", "NO_ANSWER", "CALLBACK", "ORDER_PLACED", "NOT_INTERESTED")
_PHONE_RE = re.compile(r"^[0-9+\-() ]{7,20}$")

_require_sales = require_module("SALES", "SALES_MODULE")
_require_sales_or_redirect = require_module("SALES", "SALES_MODULE", redirect_unauthenticated=True)


def _ctx(db: Session, user: User, **extra) -> dict:
    ctx = {
        "user": user, "L": _L(db, user), "unread": _unread(db, user),
    }
    ctx.update(_nav_ctx(db, user))
    ctx.update(extra)
    return ctx


def _redir(url: str):
    return RedirectResponse(url, status_code=303)


def get_customer_or_404(db: Session, customer_id: str, tenant_id: str) -> Customer:
    c = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.tenant_id == tenant_id,
        Customer.is_deleted == False,
    ).first()
    if not c:
        raise HTTPException(404, "Customer not found")
    return c


def _can_edit_customer(user: User, customer: Customer) -> bool:
    if user.role in ("ADMIN", "MANAGER"):
        return True
    return customer.assigned_agent_id == user.id


# ══════════════════════════════════════════════════════════════════════════════
# PRIORITY QUEUE
# ══════════════════════════════════════════════════════════════════════════════

def _order_aggregates(db: Session, tenant_id: str, customer_ids: list) -> dict:
    """customer_id -> {count, total, last_at} from non-cancelled sales orders."""
    if not customer_ids:
        return {}
    rows = (
        db.query(
            SalesOrder.customer_id,
            func.count(SalesOrder.id).label("cnt"),
            func.sum(SalesOrder.total_amount).label("total"),
            func.max(SalesOrder.created_at).label("last_at"),
        )
        .filter(
            SalesOrder.tenant_id == tenant_id,
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.status != "CANCELLED",
        )
        .group_by(SalesOrder.customer_id)
        .all()
    )
    return {
        r.customer_id: {"count": r.cnt, "total": r.total or 0.0, "last_at": r.last_at}
        for r in rows
    }


def get_agent_queue(
    db: Session, agent_id: str, tenant_id: str, tier: list = None, search: str = "",
    price_list_id: list = None, date_from: str = "", date_to: str = "", horizon: int = 0,
) -> dict:
    tier = tier or []
    price_list_id = price_list_id or []
    today = date.today()
    horizon_date = today + timedelta(days=horizon) if horizon else today

    last_log_sq = (
        db.query(
            CRMCallLog.customer_id,
            func.max(CRMCallLog.contacted_at).label("last_contacted"),
        )
        .filter(CRMCallLog.tenant_id == tenant_id,
                CRMCallLog.agent_id == agent_id)
        .group_by(CRMCallLog.customer_id)
        .subquery()
    )

    pending_sq = (
        db.query(
            CRMCallLog.customer_id,
            func.min(CRMCallLog.follow_up_at).label("next_follow_up"),
        )
        .filter(CRMCallLog.tenant_id == tenant_id,
                CRMCallLog.agent_id == agent_id,
                CRMCallLog.follow_up_done == False,
                CRMCallLog.follow_up_at != None)
        .group_by(CRMCallLog.customer_id)
        .subquery()
    )

    rows = (
        db.query(
            Customer,
            last_log_sq.c.last_contacted,
            pending_sq.c.next_follow_up,
        )
        .outerjoin(last_log_sq, Customer.id == last_log_sq.c.customer_id)
        .outerjoin(pending_sq, Customer.id == pending_sq.c.customer_id)
        .filter(
            Customer.tenant_id == tenant_id,
            Customer.assigned_agent_id == agent_id,
            Customer.is_deleted == False,
            Customer.is_active == True,
        )
    )

    if tier:
        rows = rows.filter(Customer.customer_tier.in_(tier))
    if search:
        like = f"%{search}%"
        rows = rows.filter((Customer.name.ilike(like)) | (Customer.phone.ilike(like)))
    if price_list_id:
        rows = rows.filter(Customer.price_list_id.in_(price_list_id))
    if date_from:
        try:
            rows = rows.filter(last_log_sq.c.last_contacted >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            rows = rows.filter(last_log_sq.c.last_contacted <= datetime.fromisoformat(date_to))
        except ValueError:
            pass

    rows = rows.all()

    order_agg = _order_aggregates(db, tenant_id, [c.id for c, _, _ in rows])

    p1, p2, p3, p4 = [], [], [], []

    for cust, last_contacted, next_follow_up in rows:
        days_since = (
            (datetime.utcnow() - last_contacted).days
            if last_contacted else 9999
        )
        freq = cust.contact_freq_days or 30
        agg = order_agg.get(cust.id, {"count": 0, "total": 0.0, "last_at": None})
        if next_follow_up:
            next_due = next_follow_up + timedelta(days=freq)
        elif last_contacted:
            next_due = last_contacted + timedelta(days=freq)
        else:
            next_due = datetime.utcnow()

        entry = {
            "customer": cust,
            "last_contacted": last_contacted,
            "days_since_contact": days_since,
            "next_follow_up": next_follow_up,
            "next_due": next_due,
            "order_count": agg["count"],
            "order_total": agg["total"],
            "last_order_at": agg["last_at"],
        }

        if next_follow_up and next_follow_up.date() <= horizon_date:
            entry["priority_label"] = "Follow-up due"
            p1.append(entry)
        elif days_since >= freq:
            entry["priority_label"] = "Overdue contact"
            p2.append(entry)
        elif days_since >= int(freq * 0.8):
            entry["priority_label"] = "Contact soon"
            p3.append(entry)
        else:
            entry["priority_label"] = "On track"
            p4.append(entry)

    tier_order = {"A": 0, "B": 1, "C": 2, "UNRANKED": 3, "D": 4}

    def sort_key(e):
        return (
            tier_order.get(e["customer"].customer_tier, 3),
            -(e["days_since_contact"]),
            -(e["order_total"]),
        )

    return {
        "p1": sorted(p1, key=sort_key),
        "p2": sorted(p2, key=sort_key),
        "p3": sorted(p3, key=sort_key),
        "p4": sorted(p4, key=sort_key),
        "total": len(p1) + len(p2) + len(p3) + len(p4),
        "overdue_count": len(p1) + len(p2),
    }


@router.get("/sales/contacts", response_class=HTMLResponse)
def contacts_queue(
    request: Request,
    agent_id: str = None,
    tier: list = Query(default=[]),
    search: str = "",
    price_list_id: list = Query(default=[]),
    date_from: str = "",
    date_to: str = "",
    horizon: int = 0,
    user: User = Depends(_require_sales_or_redirect),
    db: Session = Depends(get_db),
):
    target_agent_id = user.id
    if user.role in ("ADMIN", "MANAGER") and agent_id:
        target_agent_id = agent_id

    queue = get_agent_queue(
        db, target_agent_id, user.tenant_id, tier=tier, search=search,
        price_list_id=price_list_id, date_from=date_from, date_to=date_to,
        horizon=horizon,
    )

    agents = []
    if user.role in ("ADMIN", "MANAGER"):
        agents = [u for u in db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.is_active == True,
            User.is_deleted == False,
        ).all() if has_module(u, "SALES")]

    price_lists = db.query(PriceList).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
        PriceList.is_active == True,
    ).order_by(PriceList.name).all()

    # PWA-installed sessions get the mobile-redesigned "Priority Feed" (design
    # section 4a); desktop keeps contacts_queue.html — see tickets_list() /
    # checklists_list() in app/main.py for the same pwa_ui-cookie pattern.
    template_name = "sales/contacts_mobile.html" if request.cookies.get("pwa_ui") == "1" else "sales/contacts_queue.html"

    return templates.TemplateResponse(request, template_name, _ctx(
        db, user,
        queue=queue, agents=agents, selected_agent_id=target_agent_id,
        outcome_choices=OUTCOME_CHOICES, tier_choices=TIER_CHOICES,
        tier=tier, search=search, price_lists=price_lists,
        price_list_id=price_list_id, date_from=date_from, date_to=date_to,
        horizon=horizon, today_display=date.today().strftime('%A, %d %b %Y'),
        now=datetime.utcnow(),
    ))


# ══════════════════════════════════════════════════════════════════════════════
# CALL LOG
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sales/contacts/{customer_id}/log-call")
def log_call(
    customer_id: str,
    outcome: str = Form(...),
    notes: str = Form(""),
    follow_up_at: str = Form(""),
    contacted_at: str = Form(""),
    ajax: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    def _err(msg):
        if ajax:
            return JSONResponse({"error": msg}, status_code=400)
        return _redir(f"/sales/contacts/{customer_id}?err={msg.replace(' ', '+')}")

    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    if user.role not in ("ADMIN", "MANAGER") and customer.assigned_agent_id != user.id:
        raise HTTPException(403, "Not your assigned customer")

    if outcome not in OUTCOME_CHOICES:
        return _err("Invalid outcome")

    fu_at = None
    if follow_up_at:
        try:
            fu_at = datetime.fromisoformat(follow_up_at)
        except ValueError:
            return _err("Invalid follow-up date")

    if outcome == "CALLBACK" and not fu_at:
        return _err("Follow-up date is required for Callback")

    c_at = datetime.utcnow()
    if contacted_at:
        try:
            c_at = datetime.fromisoformat(contacted_at)
        except ValueError:
            pass

    # Logging a new call supersedes any previously scheduled follow-up for
    # this customer — whether it's overdue or still upcoming — so the queue's
    # "next follow-up" always reflects the most recent call, not a stale
    # reminder from an earlier conversation.
    db.query(CRMCallLog).filter(
        CRMCallLog.customer_id == customer_id,
        CRMCallLog.agent_id == user.id,
        CRMCallLog.follow_up_done == False,
        CRMCallLog.follow_up_at != None,
    ).update({"follow_up_done": True})

    log = CRMCallLog(
        tenant_id=user.tenant_id,
        customer_id=customer_id,
        agent_id=user.id,
        outcome=outcome,
        contacted_at=c_at,
        follow_up_at=fu_at,
        notes=notes.strip() or None,
    )
    db.add(log)

    db.query(Customer).filter(Customer.id == customer_id).update({
        "last_contacted_at": c_at,
    })

    # Collections A2: enforce the call-attempt cap only against an open case
    # (open_balance_lock) — logging calls for ordinary CRM follow-up is
    # untouched. Auto-escalates once the tenant's configured cap is reached.
    if customer.open_balance_lock:
        tenant = db.query(Tenant).get(user.tenant_id)
        cap = (tenant.collections_call_attempt_cap or 2) if tenant else 2
        new_count = (customer.collections_call_attempt_count or 0) + 1
        case_updates = {"collections_call_attempt_count": new_count}
        if new_count >= cap and not customer.collections_escalated:
            case_updates["collections_escalated"] = True
            case_updates["collections_escalated_at"] = c_at
        db.query(Customer).filter(Customer.id == customer_id).update(case_updates)

    db.commit()

    if ajax:
        return JSONResponse({
            "ok": True,
            "log": {
                "contacted_at": log.contacted_at.strftime("%d %b %Y %H:%M") if log.contacted_at else "—",
                "outcome": log.outcome,
                "outcome_label": log.outcome.replace("_", " ").title(),
                "agent": user.name,
                "follow_up_at": log.follow_up_at.strftime("%d %b %Y") if log.follow_up_at else None,
                "notes": log.notes or "",
            },
            "last_contacted_at": c_at.strftime("%d %b %Y"),
            "redirect_to_order": (outcome == "ORDER_PLACED"),
            "order_new_url": f"/sales/orders/new?customer_id={customer_id}&call_log_id={log.id}",
        })

    if outcome == "ORDER_PLACED":
        return _redir(f"/sales/orders/new?customer_id={customer_id}&call_log_id={log.id}")

    return _redir(f"/sales/contacts/{customer_id}?msg=Call+logged")


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTIONS & ESCALATION — A2-A4 (call-cap enforcement, escalation queue,
# dashboard rollups, payment status, invoice/receipt uploads).
# Every route here is a no-op unless COLLECTIONS_MODULE is enabled.
# ══════════════════════════════════════════════════════════════════════════════

_require_collections = require_module("COLLECTIONS", "COLLECTIONS_MODULE")

COLLECTIONS_DOC_TYPES = ("INVOICE", "STATEMENT", "PAYMENT_RECEIPT", "OTHER")
_ALLOWED_COLLECTIONS_DOC_MIME = {"image/jpeg", "image/png", "application/pdf"}
_MAX_COLLECTIONS_DOC_MB = 10


@router.post("/sales/contacts/{customer_id}/collections/open")
def collections_open_case(
    customer_id: str,
    due_date: str = Form(...),
    outstanding_amount: str = Form(""),
    user: User = Depends(_require_collections),
    db: Session = Depends(get_db),
):
    """Opens a collections case for this party: sets the outstanding-balance
    lock (blocks duplicate re-entry, Req #1), the due date the day-tier
    filters are computed against, and the outstanding amount the dashboard
    rollup (Req #17) reconciles against."""
    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    try:
        due = datetime.fromisoformat(due_date).date()
    except ValueError:
        return _redir(f"/sales/contacts/{customer_id}?err=Invalid+due+date")
    try:
        amount = float(outstanding_amount) if outstanding_amount else None
    except ValueError:
        amount = None
    customer.open_balance_lock = True
    customer.collections_case_due_date = due
    customer.collections_outstanding_amount = amount
    customer.collections_payment_status = "PENDING"
    customer.collections_call_attempt_count = 0
    customer.collections_escalated = False
    customer.collections_escalated_at = None
    customer.collections_last_tier_notified = None
    customer.collections_non_responsive_alerted = False
    db.commit()
    return _redir(f"/sales/contacts/{customer_id}?msg=Collections+case+opened")


@router.post("/sales/contacts/{customer_id}/collections/mark-partial")
def collections_mark_partial(
    customer_id: str,
    user: User = Depends(_require_collections),
    db: Session = Depends(get_db),
):
    """Req #16 — marks a partial payment received; the case stays open (call
    cap / escalation tracking untouched) but the status is clearly surfaced."""
    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    if not customer.open_balance_lock:
        return _redir(f"/sales/contacts/{customer_id}?err=No+open+collections+case")
    customer.collections_payment_status = "PARTIAL"
    db.commit()
    return _redir(f"/sales/contacts/{customer_id}?msg=Marked+as+partially+paid")


@router.post("/sales/contacts/{customer_id}/collections/resolve")
def collections_resolve_case(
    customer_id: str,
    user: User = Depends(_require_collections),
    db: Session = Depends(get_db),
):
    """Closes the open collections case (payment received / written off)."""
    from .collections_notify import notify_owner_payment_received

    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    tenant = db.query(Tenant).get(user.tenant_id)
    customer.open_balance_lock = False
    customer.collections_case_due_date = None
    customer.collections_outstanding_amount = None
    customer.collections_payment_status = "COMPLETED"
    customer.collections_call_attempt_count = 0
    customer.collections_escalated = False
    customer.collections_escalated_at = None
    customer.collections_last_tier_notified = None
    customer.collections_non_responsive_alerted = False
    db.commit()
    notify_owner_payment_received(db, tenant, customer)  # Req #12 — non-blocking
    return _redir(f"/sales/contacts/{customer_id}?msg=Collections+case+resolved")


async def _check_collections_doc_constraints(file: UploadFile):
    content = await file.read()
    if len(content) > _MAX_COLLECTIONS_DOC_MB * 1024 * 1024:
        raise HTTPException(413, f"'{file.filename}' is too large. Max {_MAX_COLLECTIONS_DOC_MB} MB.")
    ct = (file.content_type or "").lower()
    if ct not in _ALLOWED_COLLECTIONS_DOC_MIME:
        raise HTTPException(400, f"'{file.filename}': only JPG, PNG, or PDF files are allowed.")
    await file.seek(0)


@router.post("/sales/contacts/{customer_id}/collections/documents")
async def collections_upload_document(
    customer_id: str,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(_require_collections),
    db: Session = Depends(get_db),
):
    """Req #19 — invoice / statement / payment-receipt upload on the party
    record. Reuses the existing polymorphic MediaUpload table and the shared
    save_upload() disk-write helper — no new upload infrastructure."""
    from .database import MediaUpload
    from .uploads import save_upload

    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    if doc_type not in COLLECTIONS_DOC_TYPES:
        return _redir(f"/sales/contacts/{customer_id}?err=Invalid+document+type")
    if not file or not file.filename:
        return _redir(f"/sales/contacts/{customer_id}?err=No+file+selected")

    await _check_collections_doc_constraints(file)
    info = await save_upload(file, user.tenant_id)
    db.add(MediaUpload(
        tenant_id=user.tenant_id,
        entity_type=f"collections_document_{doc_type.lower()}",
        entity_id=customer.id,
        file_name=info["file_name"], file_path=info["file_path"],
        file_type=info["file_type"], file_size=info["file_size"],
        uploaded_by_id=user.id,
    ))
    db.commit()
    return _redir(f"/sales/contacts/{customer_id}?msg=Document+uploaded")


@router.get("/sales/collections/escalation", response_class=HTMLResponse)
def collections_escalation(
    request: Request,
    tier: str = Query(""),
    user: User = Depends(_require_collections),
    db: Session = Depends(get_db),
):
    """Collections Dashboard — outstanding/overdue rollups (Req #17) plus the
    escalation queue of cases that reached the call-attempt cap, filterable
    by overdue day-tier (Req #11, #3)."""
    tenant = db.query(Tenant).get(user.tenant_id)
    tiers = sorted(set(int(t.strip()) for t in (tenant.collections_escalation_tiers or "30,60,90").split(",") if t.strip().isdigit()))
    today = date.today()

    open_cases = db.query(Customer).filter(
        Customer.tenant_id == user.tenant_id,
        Customer.is_deleted == False,
        Customer.open_balance_lock == True,
    ).all()
    total_outstanding = sum(c.collections_outstanding_amount or 0 for c in open_cases)
    total_overdue = sum(
        c.collections_outstanding_amount or 0 for c in open_cases
        if c.collections_case_due_date and c.collections_case_due_date < today
    )

    cases = [c for c in open_cases if c.collections_escalated]
    cases.sort(key=lambda c: c.collections_escalated_at or datetime.min, reverse=True)

    rows = []
    for c in cases:
        days_overdue = (today - c.collections_case_due_date).days if c.collections_case_due_date else None
        case_tier = max([t for t in tiers if days_overdue is not None and days_overdue >= t], default=None)
        rows.append({"customer": c, "days_overdue": days_overdue, "tier": case_tier})

    if tier and tier.isdigit():
        # Cumulative filter, matching the "30+ / 60+ / 90+ days" labels —
        # a 76-day-overdue case shows up under both the 30+ and 60+ filters.
        rows = [r for r in rows if r["days_overdue"] is not None and r["days_overdue"] >= int(tier)]

    return templates.TemplateResponse(request, "sales/collections_escalation.html", _ctx(
        db, user, rows=rows, tiers=tiers, selected_tier=tier,
        total_outstanding=total_outstanding, total_overdue=total_overdue,
        open_case_count=len(open_cases),
    ))


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMER PROFILE / LIST / CREATE / EDIT / ASSIGN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/contacts/new", response_class=HTMLResponse)
def contact_create_form(
    request: Request,
    user: User = Depends(_require_sales_or_redirect),
    db: Session = Depends(get_db),
):
    agents = []
    if user.role in ("ADMIN", "MANAGER"):
        agents = [u for u in db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.is_active == True,
            User.is_deleted == False,
        ).all() if has_module(u, "SALES")]

    price_lists = db.query(PriceList).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
        PriceList.is_active == True,
    ).order_by(PriceList.name).all()

    return templates.TemplateResponse(request, "sales/contact_create.html", _ctx(
        db, user, agents=agents, tier_choices=TIER_CHOICES, price_lists=price_lists,
    ))


STAGE_CHOICES = ("p1", "p2", "p3", "p4")
STAGE_LABELS = {"p1": "Follow-up due", "p2": "Overdue contact", "p3": "Contact soon", "p4": "On track"}
STAGE_COLORS = {"p1": "#ef4444", "p2": "#f59e0b", "p3": "#eab308", "p4": "#22c55e"}


def _classify_stage(days_since: int, next_follow_up, freq: int) -> str:
    today = date.today()
    if next_follow_up and next_follow_up.date() <= today:
        return "p1"
    if days_since >= freq:
        return "p2"
    if days_since >= int(freq * 0.8):
        return "p3"
    return "p4"


@router.get("/sales/contacts/all", response_class=HTMLResponse)
def contacts_list(
    request: Request,
    page: int = 1,
    search: str = "",
    tier: list = Query(default=[]),
    agent_id: list = Query(default=[]),
    stage: list = Query(default=[]),
    status: str = "active",
    user: User = Depends(_require_sales_or_redirect),
    db: Session = Depends(get_db),
):
    q = db.query(Customer).filter(
        Customer.tenant_id == user.tenant_id,
        Customer.is_deleted == False,
    )

    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(Customer.assigned_agent_id == user.id)
    elif agent_id:
        q = q.filter(Customer.assigned_agent_id.in_(agent_id))

    if search:
        like = f"%{search}%"
        q = q.filter((Customer.name.ilike(like)) | (Customer.phone.ilike(like)))
    if tier:
        q = q.filter(Customer.customer_tier.in_(tier))
    if status == "active":
        q = q.filter(Customer.is_active == True)
    elif status == "inactive":
        q = q.filter(Customer.is_active == False)

    customers = q.order_by(Customer.name).all()

    now = datetime.utcnow()
    pending_map = {}
    if customers:
        pending_rows = (
            db.query(CRMCallLog.customer_id, func.min(CRMCallLog.follow_up_at).label("next_follow_up"))
            .filter(
                CRMCallLog.tenant_id == user.tenant_id,
                CRMCallLog.customer_id.in_([c.id for c in customers]),
                CRMCallLog.follow_up_done == False,
                CRMCallLog.follow_up_at != None,
            )
            .group_by(CRMCallLog.customer_id)
            .all()
        )
        pending_map = {r.customer_id: r.next_follow_up for r in pending_rows}

    all_rows = []
    for c in customers:
        days_since = (now - c.last_contacted_at).days if c.last_contacted_at else 9999
        next_follow_up = pending_map.get(c.id)
        freq = c.contact_freq_days or 30
        stage_key = _classify_stage(days_since, next_follow_up, freq)
        all_rows.append({
            "customer": c,
            "days_since_contact": (now - c.last_contacted_at).days if c.last_contacted_at else None,
            "next_follow_up": next_follow_up,
            "stage": stage_key,
        })

    if stage:
        all_rows = [r for r in all_rows if r["stage"] in stage]

    total = len(all_rows)
    rows = all_rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

    order_agg = _order_aggregates(db, user.tenant_id, [r["customer"].id for r in rows])
    for r in rows:
        agg = order_agg.get(r["customer"].id, {"count": 0, "total": 0.0})
        r["order_count"] = agg["count"]
        r["order_total"] = agg["total"]

    agents = []
    if user.role in ("ADMIN", "MANAGER"):
        agents = [u for u in db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.is_active == True,
            User.is_deleted == False,
        ).all() if has_module(u, "SALES")]

    price_lists = db.query(PriceList).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
        PriceList.is_active == True,
    ).order_by(PriceList.name).all()

    return templates.TemplateResponse(request, "sales/contacts_list.html", _ctx(
        db, user,
        rows=rows, total=total, page=page, page_size=PAGE_SIZE,
        search=search, tier=tier, agent_id=agent_id, status=status,
        agents=agents, tier_choices=TIER_CHOICES, price_lists=price_lists,
        stage=stage, stage_choices=STAGE_CHOICES, stage_labels=STAGE_LABELS,
        stage_colors=STAGE_COLORS,
    ))


# ══════════════════════════════════════════════════════════════════════════════
# BULK IMPORT / EXPORT  (static paths — must be declared before /{customer_id})
# ══════════════════════════════════════════════════════════════════════════════

# Same canonical column set as Setup's customer import (app/setup_routes.py:
# _CUSTOMER_COLS) so both surfaces' templates and import behavior match exactly.
_BULK_COLS = [c for c, _help in _CUSTOMER_COLS]


@router.get("/sales/contacts/bulk-upload", response_class=HTMLResponse)
def bulk_upload_form(
    request: Request,
    user: User = Depends(_require_sales_or_redirect),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(request, "sales/contacts_bulk_upload.html", _ctx(db, user))


@router.get("/sales/contacts/bulk-template")
def bulk_template(user: User = Depends(_require_sales)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_BULK_COLS)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=customers_bulk_template.csv"},
    )


def _validate_bulk_row(row: dict, tenant_id: str, db: Session, seen_phones: set):
    name = (row.get("name") or "").strip()
    phone = (row.get("phone") or "").strip()
    agent_email = (row.get("agent_email") or "").strip()
    employee_name = (row.get("employee_name") or "").strip()
    tier = (row.get("tier") or "").strip().upper()
    freq_raw = (row.get("contact_freq_days") or "").strip()
    credit_raw = (row.get("credit_limit") or "").strip()

    if not name:
        return None, "name is required"
    if not phone:
        return None, "phone is required"
    if phone in seen_phones:
        return None, f"duplicate phone {phone} within file"
    existing = db.query(Customer).filter(
        Customer.tenant_id == tenant_id, Customer.phone == phone, Customer.is_deleted == False,
    ).first()
    if existing:
        return None, f"phone {phone} already exists"

    agent, agent_err = resolve_customer_agent(db, tenant_id, agent_email, employee_name)
    if agent_err:
        return None, agent_err

    if tier and tier not in _CUSTOMER_TIERS:
        return None, "tier must be A, B, C or blank"

    freq = 30
    if freq_raw:
        try:
            freq = int(freq_raw)
            if freq <= 0:
                return None, "contact_freq_days must be a positive integer"
        except ValueError:
            return None, "contact_freq_days must be a positive integer"

    credit = None
    if credit_raw:
        try:
            credit = float(credit_raw)
        except ValueError:
            return None, "credit_limit must be a number"

    seen_phones.add(phone)
    return {
        "name": name, "phone": phone,
        "email": (row.get("email") or "").strip() or None,
        "contact_person": (row.get("contact_person") or "").strip() or None,
        "address": (row.get("address") or "").strip() or None,
        "notes": (row.get("notes") or "").strip() or None,
        "agent_id": agent.id if agent else None,
        "contact_freq_days": freq,
        "tier": tier or "UNRANKED",
        "gstin": (row.get("gstin") or "").strip() or None,
        "credit_limit": credit,
        "billing_address": (row.get("billing_address") or "").strip() or None,
        "shipping_address": (row.get("shipping_address") or "").strip() or None,
        "default_payment_terms": (row.get("default_payment_terms") or "").strip() or None,
    }, None


def _run_bulk_validation(rows_in: list, tenant_id: str, db: Session, start_index: int = 2) -> dict:
    """Shared validator for both the initial CSV upload and re-validation of edited error rows."""
    seen_phones = set()
    valid_rows, errors = [], []
    for i, row in enumerate(rows_in, start=start_index):
        parsed, error = _validate_bulk_row(row, tenant_id, db, seen_phones)
        if error:
            errors.append({"row": row.get("_row", i), "error": error, "data": dict(row)})
        else:
            valid_rows.append(parsed)
    return {
        "total": len(valid_rows) + len(errors),
        "valid": len(valid_rows),
        "errors": errors,
        "rows": valid_rows,
    }


@router.post("/sales/contacts/bulk-upload")
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
        dict_reader = csv.DictReader(io.StringIO(content))
        reader = list(dict_reader)
    except csv.Error:
        raise HTTPException(400, "Could not parse file — please upload a valid CSV using the provided template.")
    fmt_err = check_required_headers(dict_reader.fieldnames, ["name", "phone"], _BULK_COLS)
    if fmt_err:
        return JSONResponse({"format_error": fmt_err})
    if len(reader) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"File has {len(reader)} rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    for i, row in enumerate(reader, start=2):
        row["_row"] = i
    return JSONResponse(_run_bulk_validation(reader, user.tenant_id, db))


@router.post("/sales/contacts/bulk-upload/revalidate")
async def bulk_upload_revalidate(
    payload: dict,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    """Re-validate a small set of edited error rows in-browser, without re-uploading the file."""
    rows_in = payload.get("rows", [])
    if len(rows_in) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Too many rows — maximum allowed is {BULK_IMPORT_MAX_ROWS}.")
    return JSONResponse(_run_bulk_validation(rows_in, user.tenant_id, db))


@router.post("/sales/contacts/bulk-upload/confirm")
def bulk_upload_confirm(
    payload: dict,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    rows = payload.get("rows", [])
    created = 0
    skipped = 0
    warnings = []
    for r in rows:
        phone = (r.get("phone") or "").strip()
        # Re-check for a duplicate right before insert — the row may have been
        # validated earlier and this same batch re-submitted since (double-click
        # on Confirm, or a repeated import).
        if phone and db.query(Customer).filter(
            Customer.tenant_id == user.tenant_id, Customer.phone == phone, Customer.is_deleted == False,
        ).first():
            skipped += 1
            warnings.append(f"Skipped {r.get('name') or phone}: phone {phone} already exists.")
            continue
        db.add(Customer(
            tenant_id=user.tenant_id,
            name=r["name"], phone=r["phone"], email=r.get("email"),
            contact_person=r.get("contact_person"),
            address=r.get("address"),
            notes=r.get("notes"),
            created_by_id=user.id,
            assigned_agent_id=r.get("agent_id") or user.id,
            customer_tier=r.get("tier") or "UNRANKED",
            contact_freq_days=r.get("contact_freq_days") or 30,
            gstin=r.get("gstin"),
            credit_limit=r.get("credit_limit"),
            billing_address=r.get("billing_address"),
            shipping_address=r.get("shipping_address"),
            default_payment_terms=r.get("default_payment_terms"),
        ))
        created += 1
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"Import failed — no contacts were created. {e}")
    return JSONResponse({"created": created, "skipped": skipped, "warnings": warnings})


@router.get("/sales/contacts/export")
def contacts_export(
    agent_id: str = "",
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    q = db.query(Customer).filter(
        Customer.tenant_id == user.tenant_id, Customer.is_deleted == False,
    )
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(Customer.assigned_agent_id == user.id)
    elif agent_id:
        q = q.filter(Customer.assigned_agent_id == agent_id)

    customers = q.order_by(Customer.name).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "name", "contact_person", "phone", "email", "address", "notes",
        "agent_email", "tier", "contact_freq_days", "gstin", "credit_limit",
        "billing_address", "shipping_address", "default_payment_terms",
        # Informational / derived — not accepted back on import.
        "assigned_agent_name", "last_contacted_at", "days_since_contact",
    ])
    now = datetime.utcnow()
    for c in customers:
        days_since = (now - c.last_contacted_at).days if c.last_contacted_at else ""
        w.writerow([
            c.name, c.contact_person or "", c.phone or "", c.email or "",
            c.address or "", c.notes or "",
            c.assigned_agent.email if c.assigned_agent else "",
            c.customer_tier or "UNRANKED", c.contact_freq_days or 30,
            c.gstin or "", c.credit_limit or "",
            c.billing_address or "", c.shipping_address or "", c.default_payment_terms or "",
            c.assigned_agent.name if c.assigned_agent else "",
            c.last_contacted_at.isoformat() if c.last_contacted_at else "",
            days_since,
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=customers_export.csv"},
    )


@router.get("/sales/contacts/{customer_id}", response_class=HTMLResponse)
def contact_detail(
    request: Request,
    customer_id: str,
    date_from: str = "",
    date_to: str = "",
    product: str = "",
    user: User = Depends(_require_sales_or_redirect),
    db: Session = Depends(get_db),
):
    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    if user.role not in ("ADMIN", "MANAGER") and customer.assigned_agent_id != user.id:
        raise HTTPException(403, "Not your assigned customer")

    call_logs_q = db.query(CRMCallLog).filter(
        CRMCallLog.customer_id == customer_id, CRMCallLog.tenant_id == user.tenant_id,
    )

    orders_q = db.query(SalesOrder).filter(
        SalesOrder.customer_id == customer_id,
        SalesOrder.is_deleted == False,
        SalesOrder.status != "CANCELLED",
    )

    dt_from = dt_to = None
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            orders_q = orders_q.filter(SalesOrder.created_at >= dt_from)
            call_logs_q = call_logs_q.filter(CRMCallLog.contacted_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            orders_q = orders_q.filter(SalesOrder.created_at <= dt_to)
            call_logs_q = call_logs_q.filter(CRMCallLog.contacted_at <= dt_to)
        except ValueError:
            pass
    if product:
        like = f"%{product}%"
        orders_q = orders_q.join(SalesOrderItem, SalesOrderItem.order_id == SalesOrder.id) \
            .join(ProductVariant, ProductVariant.id == SalesOrderItem.variant_id) \
            .filter(ProductVariant.variant_label.ilike(like)).distinct()

    call_logs = call_logs_q.order_by(CRMCallLog.contacted_at.desc()).all()
    filtered_orders = orders_q.order_by(SalesOrder.created_at.desc()).all()
    orders = filtered_orders[:10]

    # KPIs computed from the filtered (but not row-capped) order set.
    order_count = len(filtered_orders)
    order_total = sum(o.total_amount or 0 for o in filtered_orders)
    order_dates = sorted([o.created_at for o in filtered_orders if o.created_at])
    last_order_at = order_dates[-1] if order_dates else None
    days_since_last_order = (datetime.utcnow() - last_order_at).days if last_order_at else None
    if len(order_dates) > 1:
        span_days = (order_dates[-1] - order_dates[0]).days
        avg_cycle_days = round(span_days / (len(order_dates) - 1)) if span_days else 0
    else:
        avg_cycle_days = None

    # Simple monthly trend (last 6 months) for the engagement chart.
    from collections import OrderedDict
    months = OrderedDict()
    ref = datetime.utcnow().replace(day=1)
    for i in range(5, -1, -1):
        y, m = ref.year, ref.month - i
        while m <= 0:
            m += 12
            y -= 1
        months[f"{y}-{m:02d}"] = {"orders": 0, "calls": 0}
    for o in filtered_orders:
        if o.created_at:
            k = f"{o.created_at.year}-{o.created_at.month:02d}"
            if k in months:
                months[k]["orders"] += 1
    for log in call_logs:
        if log.contacted_at:
            k = f"{log.contacted_at.year}-{log.contacted_at.month:02d}"
            if k in months:
                months[k]["calls"] += 1
    trend = [{"label": k, **v} for k, v in months.items()]

    msg = request.query_params.get("msg", "")
    err = request.query_params.get("err", "")

    agents = []
    if user.role in ("ADMIN", "MANAGER"):
        agents = [u for u in db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.is_active == True,
            User.is_deleted == False,
        ).all() if has_module(u, "SALES")]

    price_lists = db.query(PriceList).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
        PriceList.is_active == True,
    ).order_by(PriceList.name).all()

    # Req #19 — invoice/statement/payment-receipt uploads for this party.
    from .database import MediaUpload
    collections_documents = db.query(MediaUpload).filter(
        MediaUpload.tenant_id == user.tenant_id,
        MediaUpload.entity_type.like("collections_document_%"),
        MediaUpload.entity_id == customer_id,
    ).order_by(MediaUpload.created_at.desc()).all()

    # PWA-installed sessions get the mobile-redesigned "record view" (design
    # section 5a); desktop keeps contact_detail.html.
    template_name = "sales/contact_detail_mobile.html" if request.cookies.get("pwa_ui") == "1" else "sales/contact_detail.html"

    return templates.TemplateResponse(request, template_name, _ctx(
        db, user,
        customer=customer, call_logs=call_logs, orders=orders,
        outcome_choices=OUTCOME_CHOICES,
        can_edit=_can_edit_customer(user, customer),
        agents=agents, price_lists=price_lists, tier_choices=TIER_CHOICES,
        is_full_editor=user.role in ("ADMIN", "MANAGER"),
        msg=msg, err=err,
        date_from=date_from, date_to=date_to, product=product,
        order_count=order_count, order_total=order_total,
        days_since_last_order=days_since_last_order, avg_cycle_days=avg_cycle_days,
        trend=trend, now=datetime.utcnow(),
        collections_documents=collections_documents, collections_doc_types=COLLECTIONS_DOC_TYPES,
    ))


@router.get("/sales/contacts/{customer_id}/edit", response_class=HTMLResponse)
def contact_edit_form(
    request: Request,
    customer_id: str,
    user: User = Depends(_require_sales_or_redirect),
    db: Session = Depends(get_db),
):
    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    if not _can_edit_customer(user, customer):
        raise HTTPException(403, "Not authorized to edit this customer")

    agents = []
    if user.role in ("ADMIN", "MANAGER"):
        agents = [u for u in db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.is_active == True,
            User.is_deleted == False,
        ).all() if has_module(u, "SALES")]

    price_lists = db.query(PriceList).filter(
        PriceList.tenant_id == user.tenant_id, PriceList.is_deleted == False,
        PriceList.is_active == True,
    ).order_by(PriceList.name).all()

    return templates.TemplateResponse(request, "sales/contact_edit.html", _ctx(
        db, user, customer=customer, agents=agents,
        tier_choices=TIER_CHOICES, price_lists=price_lists,
        is_full_editor=user.role in ("ADMIN", "MANAGER"),
    ))


@router.post("/sales/contacts/{customer_id}/edit")
def contact_edit_save(
    customer_id: str,
    name: str = Form(""),
    contact_person: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    billing_address: str = Form(""),
    shipping_address: str = Form(""),
    customer_tier: str = Form(""),
    contact_freq_days: str = Form(""),
    gstin: str = Form(""),
    credit_limit: str = Form(""),
    assigned_agent_id: str = Form(""),
    price_list_id: str = Form(""),
    default_payment_terms: str = Form(""),
    is_active: str = Form("1"),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    if not _can_edit_customer(user, customer):
        raise HTTPException(403, "Not authorized to edit this customer")

    is_full_editor = user.role in ("ADMIN", "MANAGER")

    if is_full_editor:
        if not name.strip():
            return _redir(f"/sales/contacts/{customer_id}?err=Name+is+required")
        p = phone.strip()
        if p and not _PHONE_RE.match(p):
            return _redir(f"/sales/contacts/{customer_id}?err=Invalid+phone+number+format")
        customer.name = name.strip()
        customer.contact_person = contact_person.strip() or None
        customer.phone = p or None
        customer.email = email.strip() or None
        customer.customer_tier = customer_tier or "UNRANKED"
        customer.gstin = gstin.strip() or None
        customer.assigned_agent_id = assigned_agent_id or None
        customer.price_list_id = price_list_id or None
        customer.default_payment_terms = default_payment_terms.strip() or None
        customer.is_active = is_active == "1"
        try:
            customer.contact_freq_days = int(contact_freq_days) if contact_freq_days else 30
        except ValueError:
            customer.contact_freq_days = 30
        try:
            customer.credit_limit = float(credit_limit) if credit_limit else None
        except ValueError:
            customer.credit_limit = None

    customer.notes = notes.strip() or None
    customer.billing_address = billing_address.strip() or None
    customer.shipping_address = shipping_address.strip() or None
    customer.updated_at = datetime.utcnow()
    db.commit()

    return _redir(f"/sales/contacts/{customer_id}?msg=Customer+updated")


@router.post("/sales/contacts/create")
def contact_create(
    name: str = Form(...),
    contact_person: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    gstin: str = Form(""),
    credit_limit: str = Form(""),
    billing_address: str = Form(""),
    shipping_address: str = Form(""),
    customer_tier: str = Form("UNRANKED"),
    contact_freq_days: str = Form("30"),
    assigned_agent_id: str = Form(""),
    default_payment_terms: str = Form(""),
    price_list_id: str = Form(""),
    notes: str = Form(""),
    source: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    err_redirect = "/sales/contacts" if source == "queue" else "/sales/contacts/all"
    if not name.strip():
        return _redir(f"{err_redirect}?err=Name+is+required")
    p = phone.strip()
    if p and not _PHONE_RE.match(p):
        return _redir(f"{err_redirect}?err=Invalid+phone+number+format")

    # Collections A1 (Req #1): block a duplicate party entry for the same
    # phone number while an earlier record for that party has an open,
    # unresolved balance (open_balance_lock).
    if p:
        locked = db.query(Customer).filter(
            Customer.tenant_id == user.tenant_id,
            Customer.phone == p,
            Customer.is_deleted == False,
            Customer.open_balance_lock == True,
        ).first()
        if locked:
            return _redir(f"{err_redirect}?err=A+party+with+this+phone+number+has+an+outstanding+balance+and+cannot+be+re-added")

    agent_id = assigned_agent_id if user.role in ("ADMIN", "MANAGER") and assigned_agent_id else user.id
    try:
        freq = int(contact_freq_days) if contact_freq_days else 30
    except ValueError:
        freq = 30
    try:
        credit = float(credit_limit) if credit_limit else None
    except ValueError:
        credit = None

    c = Customer(
        tenant_id=user.tenant_id,
        name=name.strip(), contact_person=contact_person.strip() or None,
        phone=p or None, email=email.strip() or None,
        notes=notes.strip() or None,
        created_by_id=user.id,
        assigned_agent_id=agent_id,
        customer_tier=customer_tier or "UNRANKED",
        contact_freq_days=freq,
        gstin=gstin.strip() or None,
        credit_limit=credit,
        billing_address=billing_address.strip() or None,
        shipping_address=shipping_address.strip() or None,
        default_payment_terms=default_payment_terms.strip() or None,
        price_list_id=price_list_id or None,
    )
    db.add(c)
    db.commit()
    return _redir(f"/sales/contacts/{c.id}?msg=Customer+created")


@router.post("/sales/contacts/{customer_id}/assign")
def contact_assign(
    customer_id: str,
    agent_id: str = Form(...),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(403, "Manager or Admin only")
    customer = get_customer_or_404(db, customer_id, user.tenant_id)

    agent = db.query(User).filter(
        User.id == agent_id, User.tenant_id == user.tenant_id,
        User.is_active == True, User.is_deleted == False,
    ).first()
    if not agent or not has_module(agent, "SALES"):
        return _redir(f"/sales/contacts/{customer_id}?err=Invalid+agent")

    customer.assigned_agent_id = agent_id
    customer.updated_at = datetime.utcnow()
    db.commit()
    return _redir(f"/sales/contacts/{customer_id}?msg=Reassigned")


# ══════════════════════════════════════════════════════════════════════════════
# FOLLOW-UP REMINDER SCHEDULER JOB
# ══════════════════════════════════════════════════════════════════════════════

def send_follow_up_reminders(db: Session):
    """
    Called by scheduler daily at 5 PM IST (11:30 UTC).
    For each agent with SALES access with overdue/unactioned follow-ups,
    send in-app notification to agent + manager, and WhatsApp to the agent.
    """
    from .notifications import create_notification
    from .constants import WHATSAPP_TEMPLATES, has_feature

    today = date.today()

    tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
    for tenant in tenants:
        if not has_feature(tenant, "SALES_MODULE", db):
            continue

        agents = [u for u in db.query(User).filter(
            User.tenant_id == tenant.id,
            User.is_active == True,
            User.is_deleted == False,
            User.role == "EMPLOYEE",
        ).all() if has_module(u, "SALES")]

        for agent in agents:
            overdue = (
                db.query(CRMCallLog)
                .join(Customer, CRMCallLog.customer_id == Customer.id)
                .filter(
                    CRMCallLog.tenant_id == tenant.id,
                    CRMCallLog.agent_id == agent.id,
                    CRMCallLog.follow_up_done == False,
                    CRMCallLog.follow_up_at != None,
                    func.date(CRMCallLog.follow_up_at) <= today,
                )
                .all()
            )

            if not overdue:
                continue

            count = len(overdue)
            unique_names = list({log.customer.name for log in overdue})
            if count > 3:
                names_sample = ", ".join(unique_names[:3]) + f" and {count - 3} more"
            else:
                names_sample = ", ".join(unique_names)

            create_notification(
                db=db, tenant_id=tenant.id, user_id=agent.id,
                notif_type="FOLLOW_UP_REMINDER",
                title=f"You have {count} follow-up(s) due today",
                body=f"Customers: {names_sample}",
                link="/sales/contacts",
            )

            if agent.manager_id:
                create_notification(
                    db=db, tenant_id=tenant.id, user_id=agent.manager_id,
                    notif_type="AGENT_FOLLOWUP_OVERDUE",
                    title=f"{agent.name} has {count} overdue follow-up(s)",
                    body=f"Customers: {names_sample}",
                    link=f"/sales/contacts?agent_id={agent.id}",
                )

            if (WHATSAPP_TEMPLATES["omniflow_follow_up_reminder"]["gupshup_template_id"]
                    and agent.whatsapp_opt_in_status in ("OPTED_IN", "MANUALLY_VERIFIED")):
                from .services.gupshup import send_whatsapp_template
                send_whatsapp_template(
                    tenant, agent.phone, "omniflow_follow_up_reminder",
                    [agent.name, str(count), names_sample],
                )

    db.commit()
