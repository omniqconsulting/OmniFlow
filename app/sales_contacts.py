"""
Sales CRM & Contacts — Brief 04.
Priority work queue, call logging, customer profile, bulk import/export,
follow-up reminder scheduler job.
"""
import csv
import io
import re
from datetime import datetime, date

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import get_db, new_id, Customer, CRMCallLog, User, Tenant, SalesOrder, PriceList
from .auth import get_current_user, has_module
from .templates_env import templates
from .setup_routes import _nav_ctx, _L, _unread

router = APIRouter()

PAGE_SIZE = 30
TIER_CHOICES = ("A", "B", "C", "UNRANKED")
OUTCOME_CHOICES = ("CONNECTED", "NO_ANSWER", "CALLBACK", "ORDER_PLACED", "NOT_INTERESTED")
_PHONE_RE = re.compile(r"^[0-9+\-() ]{7,20}$")


def _require_sales(user: User = Depends(get_current_user)) -> User:
    if not has_module(user, "SALES"):
        raise HTTPException(status_code=403, detail="Sales module not enabled for this user")
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

def get_agent_queue(db: Session, agent_id: str, tenant_id: str) -> dict:
    today = date.today()

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
        .all()
    )

    p1, p2, p3, p4 = [], [], [], []

    for cust, last_contacted, next_follow_up in rows:
        days_since = (
            (datetime.utcnow() - last_contacted).days
            if last_contacted else 9999
        )
        freq = cust.contact_freq_days or 30

        entry = {
            "customer": cust,
            "last_contacted": last_contacted,
            "days_since_contact": days_since,
            "next_follow_up": next_follow_up,
        }

        if next_follow_up and next_follow_up.date() <= today:
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
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    target_agent_id = user.id
    if user.role in ("ADMIN", "MANAGER") and agent_id:
        target_agent_id = agent_id

    queue = get_agent_queue(db, target_agent_id, user.tenant_id)

    agents = []
    if user.role in ("ADMIN", "MANAGER"):
        agents = [u for u in db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.is_active == True,
            User.is_deleted == False,
        ).all() if has_module(u, "SALES")]

    return templates.TemplateResponse(request, "sales/contacts_queue.html", _ctx(
        db, user,
        queue=queue, agents=agents, selected_agent_id=target_agent_id,
        outcome_choices=OUTCOME_CHOICES,
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
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    if user.role not in ("ADMIN", "MANAGER") and customer.assigned_agent_id != user.id:
        raise HTTPException(403, "Not your assigned customer")

    if outcome not in OUTCOME_CHOICES:
        return _redir(f"/sales/contacts/{customer_id}?err=Invalid+outcome")

    fu_at = None
    if follow_up_at:
        try:
            fu_at = datetime.fromisoformat(follow_up_at)
        except ValueError:
            return _redir(f"/sales/contacts/{customer_id}?err=Invalid+follow-up+date")

    if outcome == "CALLBACK" and not fu_at:
        return _redir(f"/sales/contacts/{customer_id}?err=Follow-up+date+is+required+for+Callback")

    c_at = datetime.utcnow()
    if contacted_at:
        try:
            c_at = datetime.fromisoformat(contacted_at)
        except ValueError:
            pass

    db.query(CRMCallLog).filter(
        CRMCallLog.customer_id == customer_id,
        CRMCallLog.agent_id == user.id,
        CRMCallLog.follow_up_done == False,
        CRMCallLog.follow_up_at != None,
        CRMCallLog.follow_up_at <= datetime.utcnow(),
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

    db.commit()

    if outcome == "ORDER_PLACED":
        return _redir(f"/sales/orders/new?customer_id={customer_id}&call_log_id={log.id}")

    return _redir(f"/sales/contacts/{customer_id}?msg=Call+logged")


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMER PROFILE / LIST / CREATE / EDIT / ASSIGN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales/contacts/new", response_class=HTMLResponse)
def contact_create_form(
    request: Request,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    agents = []
    if user.role in ("ADMIN", "MANAGER"):
        agents = [u for u in db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.is_active == True,
            User.is_deleted == False,
        ).all() if has_module(u, "SALES")]

    return templates.TemplateResponse(request, "sales/contact_create.html", _ctx(
        db, user, agents=agents, tier_choices=TIER_CHOICES,
    ))


@router.get("/sales/contacts/all", response_class=HTMLResponse)
def contacts_list(
    request: Request,
    page: int = 1,
    search: str = "",
    tier: str = "",
    agent_id: str = "",
    status: str = "active",
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    q = db.query(Customer).filter(
        Customer.tenant_id == user.tenant_id,
        Customer.is_deleted == False,
    )

    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(Customer.assigned_agent_id == user.id)
    elif agent_id:
        q = q.filter(Customer.assigned_agent_id == agent_id)

    if search:
        like = f"%{search}%"
        q = q.filter((Customer.name.ilike(like)) | (Customer.phone.ilike(like)))
    if tier:
        q = q.filter(Customer.customer_tier == tier)
    if status == "active":
        q = q.filter(Customer.is_active == True)
    elif status == "inactive":
        q = q.filter(Customer.is_active == False)

    q = q.order_by(Customer.name)
    total = q.count()
    customers = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    now = datetime.utcnow()
    rows = [{
        "customer": c,
        "days_since_contact": (now - c.last_contacted_at).days if c.last_contacted_at else None,
    } for c in customers]

    agents = []
    if user.role in ("ADMIN", "MANAGER"):
        agents = [u for u in db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.is_active == True,
            User.is_deleted == False,
        ).all() if has_module(u, "SALES")]

    return templates.TemplateResponse(request, "sales/contacts_list.html", _ctx(
        db, user,
        rows=rows, total=total, page=page, page_size=PAGE_SIZE,
        search=search, tier=tier, agent_id=agent_id, status=status,
        agents=agents, tier_choices=TIER_CHOICES,
    ))


# ══════════════════════════════════════════════════════════════════════════════
# BULK IMPORT / EXPORT  (static paths — must be declared before /{customer_id})
# ══════════════════════════════════════════════════════════════════════════════

_BULK_COLS = [
    "name", "phone", "email", "agent_email", "contact_freq_days",
    "tier", "gstin", "credit_limit", "billing_address", "shipping_address",
]


@router.get("/sales/contacts/bulk-upload", response_class=HTMLResponse)
def bulk_upload_form(
    request: Request,
    user: User = Depends(_require_sales),
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

    agent = None
    if agent_email:
        agent = db.query(User).filter(
            User.tenant_id == tenant_id, User.email == agent_email,
            User.is_active == True, User.is_deleted == False,
        ).first()
        if not agent or not has_module(agent, "SALES"):
            return None, f"agent_email {agent_email} not found or lacks SALES access"

    if tier and tier not in ("A", "B", "C"):
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
        "agent_id": agent.id if agent else None,
        "contact_freq_days": freq,
        "tier": tier or "UNRANKED",
        "gstin": (row.get("gstin") or "").strip() or None,
        "credit_limit": credit,
        "billing_address": (row.get("billing_address") or "").strip() or None,
        "shipping_address": (row.get("shipping_address") or "").strip() or None,
    }, None


@router.post("/sales/contacts/bulk-upload")
async def bulk_upload(
    file: UploadFile = File(...),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    seen_phones = set()
    valid_rows, errors = [], []

    for i, row in enumerate(reader, start=2):
        parsed, error = _validate_bulk_row(row, user.tenant_id, db, seen_phones)
        if error:
            errors.append({"row": i, "error": error, "data": dict(row)})
        else:
            valid_rows.append(parsed)

    return JSONResponse({
        "total": len(valid_rows) + len(errors),
        "valid": len(valid_rows),
        "errors": errors,
        "rows": valid_rows,
    })


@router.post("/sales/contacts/bulk-upload/confirm")
def bulk_upload_confirm(
    payload: dict,
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    rows = payload.get("rows", [])
    created = 0
    for r in rows:
        db.add(Customer(
            tenant_id=user.tenant_id,
            name=r["name"], phone=r["phone"], email=r.get("email"),
            created_by_id=user.id,
            assigned_agent_id=r.get("agent_id") or user.id,
            customer_tier=r.get("tier") or "UNRANKED",
            contact_freq_days=r.get("contact_freq_days") or 30,
            gstin=r.get("gstin"),
            credit_limit=r.get("credit_limit"),
            billing_address=r.get("billing_address"),
            shipping_address=r.get("shipping_address"),
        ))
        created += 1
    db.commit()
    return JSONResponse({"created": created})


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
        "name", "phone", "email", "tier", "assigned_agent_name", "last_contacted_at",
        "days_since_contact", "contact_freq_days", "credit_limit", "gstin", "billing_address",
    ])
    now = datetime.utcnow()
    for c in customers:
        days_since = (now - c.last_contacted_at).days if c.last_contacted_at else ""
        w.writerow([
            c.name, c.phone or "", c.email or "", c.customer_tier or "UNRANKED",
            c.assigned_agent.name if c.assigned_agent else "",
            c.last_contacted_at.isoformat() if c.last_contacted_at else "",
            days_since, c.contact_freq_days or 30, c.credit_limit or "",
            c.gstin or "", c.billing_address or "",
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
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    customer = get_customer_or_404(db, customer_id, user.tenant_id)
    if user.role not in ("ADMIN", "MANAGER") and customer.assigned_agent_id != user.id:
        raise HTTPException(403, "Not your assigned customer")

    call_logs = (
        db.query(CRMCallLog)
        .filter(CRMCallLog.customer_id == customer_id, CRMCallLog.tenant_id == user.tenant_id)
        .order_by(CRMCallLog.contacted_at.desc())
        .all()
    )

    orders = (
        db.query(SalesOrder)
        .filter(SalesOrder.customer_id == customer_id,
                SalesOrder.is_deleted  == False)
        .order_by(SalesOrder.created_at.desc())
        .limit(10)
        .all()
    )

    msg = request.query_params.get("msg", "")
    err = request.query_params.get("err", "")

    return templates.TemplateResponse(request, "sales/contact_detail.html", _ctx(
        db, user,
        customer=customer, call_logs=call_logs, orders=orders,
        outcome_choices=OUTCOME_CHOICES,
        can_edit=_can_edit_customer(user, customer),
        msg=msg, err=err,
    ))


@router.get("/sales/contacts/{customer_id}/edit", response_class=HTMLResponse)
def contact_edit_form(
    request: Request,
    customer_id: str,
    user: User = Depends(_require_sales),
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
            return _redir(f"/sales/contacts/{customer_id}/edit?err=Name+is+required")
        p = phone.strip()
        if p and not _PHONE_RE.match(p):
            return _redir(f"/sales/contacts/{customer_id}/edit?err=Invalid+phone+number+format")
        customer.name = name.strip()
        customer.contact_person = contact_person.strip() or None
        customer.phone = p or None
        customer.email = email.strip() or None
        customer.customer_tier = customer_tier or "UNRANKED"
        customer.gstin = gstin.strip() or None
        customer.assigned_agent_id = assigned_agent_id or None
        customer.price_list_id = price_list_id or None
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
    notes: str = Form(""),
    user: User = Depends(_require_sales),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return _redir("/sales/contacts/all?err=Name+is+required")
    p = phone.strip()
    if p and not _PHONE_RE.match(p):
        return _redir("/sales/contacts/all?err=Invalid+phone+number+format")

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

            if (WHATSAPP_TEMPLATES["omniflow_follow_up_reminder"]["msg91_template_id"]
                    and agent.mobile_verified):
                from .services.msg91 import send_whatsapp_template
                send_whatsapp_template(
                    agent.phone, "omniflow_follow_up_reminder",
                    [agent.name, str(count), names_sample],
                )

    db.commit()
