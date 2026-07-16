"""
Super Admin Portal — Phase 0-H
All routes are prefixed /superadmin and use the sa_token cookie.
"""
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime, date as _date
import json as _json, os, secrets, io
from markupsafe import Markup as _Markup

from .database import (
    get_db, new_id, seed_default_uoms,
    SuperAdmin, Tenant, User, WhatsAppMessageLog, WhatsAppConsentEvent,
    Ticket, ChecklistTemplate, ChecklistAssignment,
    TenantFeatureOverride, TenantLabelConfig, PlanUpgradeRequest,
    FMSFlow, FMSStage, FMSTicket, LibraryFlowTemplate, TenantDeployedItem,
    TenantAIUsage, LoginEvent,
)
from .services.qr_optin import build_opt_in_link
from .constants import OMNIFLOW_PUBLIC_DOMAIN
from .labels import INDUSTRY_NAMES, INDUSTRY_PRESETS as _PRESETS
from .constants import (
    FEATURE_CATALOG, PLAN_LIMITS, PLAN_LABELS, PLAN_ORDER,
    LIMIT_LABELS, has_feature, get_plan_features,
)
from .superadmin_auth import (
    sa_hash, sa_verify, sa_create_token, get_current_sa,
    COOKIE,
)
from .auth import hash_password

router = APIRouter(prefix="/superadmin")

from .templates_env import templates  # shared instance — has all filters






def _redirect(path: str):
    return RedirectResponse(path, status_code=302)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pending_tenants(db: Session):
    """Return all self-registered tenants awaiting approval."""
    return db.query(Tenant).filter(
        Tenant.is_approved == False,
        Tenant.is_suspended == False,
    ).order_by(Tenant.trial_started_at).all()


def _tenant_stats(db: Session, tenant_id: str) -> dict:
    """Return quick stats for one tenant."""
    users   = db.query(User).filter(User.tenant_id == tenant_id,
                                    User.is_deleted == False).count()
    tickets = db.query(Ticket).filter(Ticket.tenant_id == tenant_id,
                                      Ticket.is_deleted == False).count()
    checklists = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == tenant_id).count()
    flow_tickets = db.query(FMSTicket).filter(
        FMSTicket.tenant_id == tenant_id,
        FMSTicket.is_deleted == False).count()
    last_user = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.is_deleted == False,
        User.last_login.isnot(None),
    ).order_by(User.last_login.desc()).first()
    last_activity = last_user.last_login if last_user else None
    return {
        "users": users, "tickets": tickets, "checklists": checklists,
        "flow_tickets": flow_tickets, "last_activity": last_activity,
    }


# ── Setup (first-run wizard) ──────────────────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse)
def sa_setup_page(request: Request, db: Session = Depends(get_db)):
    # Always accessible — never disappears. Keep this URL private.
    existing_count = db.query(SuperAdmin).count()
    return templates.TemplateResponse(request, "superadmin/setup.html",
                                      {"error": None, "existing_count": existing_count})


@router.post("/setup")
def sa_setup(request: Request, name: str = Form(...), email: str = Form(...),
             password: str = Form(...), confirm: str = Form(...),
             db: Session = Depends(get_db)):
    existing_count = db.query(SuperAdmin).count()
    if password != confirm:
        return templates.TemplateResponse(request, "superadmin/setup.html",
                                          {"error": "Passwords do not match",
                                           "existing_count": existing_count})
    if db.query(SuperAdmin).filter(SuperAdmin.email == email).first():
        return templates.TemplateResponse(request, "superadmin/setup.html",
                                          {"error": "An account with that email already exists.",
                                           "existing_count": existing_count})
    sa = SuperAdmin(name=name, email=email, password_hash=sa_hash(password))
    db.add(sa)
    db.commit()
    token = sa_create_token(sa.id)
    resp  = _redirect("/superadmin/dashboard")
    resp.set_cookie(COOKIE, token, httponly=True, max_age=28800)
    return resp


# ── Login / Logout ────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def sa_login_page(request: Request, db: Session = Depends(get_db)):
    no_accounts = db.query(SuperAdmin).count() == 0
    return templates.TemplateResponse(request, "superadmin/login.html",
                                      {"error": None, "no_accounts": no_accounts})


@router.post("/login")
def sa_login(request: Request, email: str = Form(...), password: str = Form(...),
             db: Session = Depends(get_db)):
    sa = db.query(SuperAdmin).filter(SuperAdmin.email == email,
                                     SuperAdmin.is_active == True).first()
    if not sa or not sa_verify(password, sa.password_hash):
        return templates.TemplateResponse(request, "superadmin/login.html",
                                          {"error": "Invalid credentials"})
    sa.last_login = datetime.utcnow()
    db.commit()
    token = sa_create_token(sa.id)
    resp  = _redirect("/superadmin/dashboard")
    resp.set_cookie(COOKIE, token, httponly=True, max_age=28800)
    return resp


@router.get("/logout")
def sa_logout():
    resp = _redirect("/superadmin/login")
    resp.delete_cookie(COOKIE)
    return resp


# ── Platform Dashboard ────────────────────────────────────────────────────────

@router.get("/onboarding-guide", response_class=HTMLResponse)
def sa_onboarding_guide(request: Request, sa: SuperAdmin = Depends(get_current_sa)):
    return templates.TemplateResponse(request, "superadmin/onboarding_guide.html",
                                      {"sa": sa})


@router.get("/dashboard", response_class=HTMLResponse)
def sa_dashboard(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                 db: Session = Depends(get_db)):
    tenants      = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    total_users  = db.query(User).filter(User.is_deleted == False).count()
    total_tickets= db.query(Ticket).filter(Ticket.is_deleted == False).count()
    open_tickets = db.query(Ticket).filter(Ticket.is_deleted == False,
                                           Ticket.status.notin_(["CLOSED","DONE"])).count()
    plan_counts  = {"STARTER": 0, "PROFESSIONAL": 0, "ENTERPRISE": 0}
    active_plan_counts = {"STARTER": 0, "PROFESSIONAL": 0, "ENTERPRISE": 0}
    suspended    = 0
    active_count = 0
    industry_counts: dict = {}
    for t in tenants:
        plan_counts[t.plan or "STARTER"] = plan_counts.get(t.plan or "STARTER", 0) + 1
        if t.is_suspended:
            suspended += 1
        else:
            active_count += 1
            key = t.plan or "STARTER"
            active_plan_counts[key] = active_plan_counts.get(key, 0) + 1
            ind = t.industry or "Other"
            industry_counts[ind] = industry_counts.get(ind, 0) + 1

    active_users  = db.query(User).filter(User.is_deleted == False, User.is_active == True).count()

    # Recent tenants (last 5)
    recent = tenants[:5]
    recent_stats = [(t, _tenant_stats(db, t.id)) for t in recent]

    pending = _pending_tenants(db)

    upgrade_requests = db.query(PlanUpgradeRequest).filter(
        PlanUpgradeRequest.status == "PENDING"
    ).order_by(PlanUpgradeRequest.created_at.desc()).all()

    return templates.TemplateResponse(request, "superadmin/dashboard.html", {
        "sa": sa,
        "total_tenants": len(tenants),
        "active_tenants": active_count,
        "total_users": total_users,
        "active_users": active_users,
        "total_tickets": total_tickets,
        "open_tickets": open_tickets,
        "plan_counts": plan_counts,
        "active_plan_counts": active_plan_counts,
        "suspended": suspended,
        "industry_counts": industry_counts,
        "recent_stats": recent_stats,
        "pending": pending,
        "pending_count": len(pending),
        "upgrade_requests": upgrade_requests,
        "now": datetime.utcnow(),
    })


# ── Tenant List ───────────────────────────────────────────────────────────────

@router.get("/tenants", response_class=HTMLResponse)
def sa_tenant_list(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                   db: Session = Depends(get_db),
                   q: str = "", plan: str = "", status: str = ""):
    query = db.query(Tenant)
    if q:
        query = query.filter(
            Tenant.name.ilike(f"%{q}%") | Tenant.slug.ilike(f"%{q}%")
        )
    if plan:
        query = query.filter(Tenant.plan == plan)
    if status == "suspended":
        query = query.filter(Tenant.is_suspended == True)
    elif status == "active":
        query = query.filter(Tenant.is_suspended == False)

    tenants = query.order_by(Tenant.created_at.desc()).all()
    rows = [(t, _tenant_stats(db, t.id)) for t in tenants]

    pending = _pending_tenants(db)
    return templates.TemplateResponse(request, "superadmin/tenants.html", {
        "sa": sa, "rows": rows,
        "q": q, "plan_filter": plan, "status_filter": status,
        "pending_count": len(pending),
        "now": datetime.utcnow(),
    })


# ── Create Tenant ─────────────────────────────────────────────────────────────

@router.get("/tenants/check-slug")
def sa_check_slug(slug: str, db: Session = Depends(get_db),
                  sa: SuperAdmin = Depends(get_current_sa)):
    """Real-time slug availability check — called by the tenant creation form."""
    from fastapi.responses import JSONResponse as _JSON
    exists = db.query(Tenant).filter(Tenant.slug == slug).first()
    return _JSON({"available": exists is None})


@router.get("/tenants/new", response_class=HTMLResponse)
def sa_new_tenant_page(request: Request, sa: SuperAdmin = Depends(get_current_sa)):
    return templates.TemplateResponse(request, "superadmin/tenant_new.html",
                                      {"sa": sa, "error": None,
                                       "industry_names": INDUSTRY_NAMES})


@router.post("/tenants/new")
def sa_new_tenant(request: Request,
                  factory_name: str = Form(...), slug: str = Form(...),
                  industry: str = Form(""), plan: str = Form("STARTER"),
                  contact_name: str = Form(""), contact_email: str = Form(""),
                  admin_name: str = Form(...), admin_phone: str = Form(...),
                  admin_password: str = Form(...),
                  # Module selection — each checkbox sends "on" if checked
                  mod_checklists: str = Form(""), mod_fms: str = Form(""),
                  mod_inventory: str = Form(""), mod_ai: str = Form(""),
                  sa: SuperAdmin = Depends(get_current_sa),
                  db: Session = Depends(get_db)):
    if db.query(Tenant).filter(Tenant.slug == slug).first():
        return templates.TemplateResponse(request, "superadmin/tenant_new.html",
                                          {"sa": sa, "error": "Slug already taken",
                                           "industry_names": INDUSTRY_NAMES})
    tenant = Tenant(
        name=factory_name, slug=slug, industry=industry or None,
        plan=plan, contact_name=contact_name or None,
        contact_email=contact_email or None,
        is_approved=True,
    )
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id, name=admin_name, phone=admin_phone,
        password_hash=hash_password(admin_password), role="ADMIN",
    )
    db.add(admin)
    # Auto-apply label preset
    if industry and industry in _PRESETS:
        overrides = _PRESETS[industry]
        def _get(c, i):
            e = overrides.get(c); return e[i] if e else None
        db.add(TenantLabelConfig(
            tenant_id=tenant.id, industry=industry,
            ticket_s=_get("ticket",0),      ticket_p=_get("ticket",1),
            checklist_s=_get("checklist",0),checklist_p=_get("checklist",1),
            branch_s=_get("branch",0),      branch_p=_get("branch",1),
            department_s=_get("department",0),department_p=_get("department",1),
            employee_s=_get("employee",0),  employee_p=_get("employee",1),
        ))
    # Write explicit module overrides — SA must opt-in each module at creation time.
    # Tickets are always on (core). All others are set explicitly so plan defaults
    # don't silently enable modules the client doesn't need.
    _module_flags = {
        "CHECKLISTS": bool(mod_checklists),
        "FMS":        bool(mod_fms),
        "INVENTORY":  bool(mod_inventory),
        "ASK_AI":     bool(mod_ai),
    }
    for feature, enabled in _module_flags.items():
        db.add(TenantFeatureOverride(
            tenant_id=tenant.id, feature=feature, enabled=enabled,
            note="Set at tenant creation by SA",
        ))
    seed_default_uoms(db, tenant.id)
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant.id}?msg=created")


# ── Tenant Detail ─────────────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}", response_class=HTMLResponse)
def sa_tenant_detail(request: Request, tenant_id: str,
                     sa: SuperAdmin = Depends(get_current_sa),
                     db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    stats  = _tenant_stats(db, tenant_id)
    users  = db.query(User).filter(User.tenant_id == tenant_id,
                                   User.is_deleted == False).order_by(User.created_at).all()
    recent_tickets = db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id, Ticket.is_deleted == False,
    ).order_by(Ticket.created_at.desc()).limit(10).all()

    label_row = db.query(TenantLabelConfig).filter(
        TenantLabelConfig.tenant_id == tenant_id).first()
    # Phase 0-K-8: deployed items with update-available flags
    from .superadmin_library import get_deployed_items_for_tenant
    deployed_items = get_deployed_items_for_tenant(db, tenant_id)
    updates_available = [d for d in deployed_items if d["update_available"]]

    # FMS flows for this tenant
    fms_flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tenant_id,
        FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.name).all()
    # Annotate with active ticket count
    for f in fms_flows:
        f.active_ticket_count = db.query(FMSTicket).filter(
            FMSTicket.flow_id == f.id,
            FMSTicket.is_deleted == False,
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        ).count()

    # Plan flow limit check
    plan = tenant.plan or "STARTER"
    fms_flow_limit = PLAN_LIMITS.get(plan, {}).get("max_fms_flows")
    fms_flow_count = len(fms_flows)

    # Library flows available to deploy
    lib_flows = db.query(LibraryFlowTemplate).filter(
        LibraryFlowTemplate.status == "ACTIVE"
    ).all() if hasattr(LibraryFlowTemplate, "status") else db.query(LibraryFlowTemplate).all()

    # Checklists for this tenant
    checklist_templates = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.tenant_id == tenant_id,
        ChecklistTemplate.is_deleted == False,
    ).order_by(ChecklistTemplate.created_at.desc()).all()
    for ct in checklist_templates:
        ct.total_assignments = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == ct.id).count()
        ct.done_assignments = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id == ct.id,
            ChecklistAssignment.status == "DONE").count()

    # FMS tickets for this tenant
    fms_tickets = db.query(FMSTicket).filter(
        FMSTicket.tenant_id == tenant_id,
        FMSTicket.is_deleted == False,
    ).order_by(FMSTicket.created_at.desc()).limit(20).all()

    # AI usage for this tenant
    from datetime import date as _date
    from .constants import get_limit as _get_limit
    today_str = _date.today().isoformat()
    month_str = today_str[:7]  # "YYYY-MM"
    ai_today_row = db.query(TenantAIUsage).filter(
        TenantAIUsage.tenant_id == tenant_id,
        TenantAIUsage.date == today_str,
    ).first()
    ai_usage_today = ai_today_row.call_count if ai_today_row else 0
    ai_month_rows = db.query(TenantAIUsage).filter(
        TenantAIUsage.tenant_id == tenant_id,
        TenantAIUsage.date.like(f"{month_str}%"),
    ).all()
    ai_usage_month = sum(r.call_count for r in ai_month_rows)
    # Real plan default from constants (0 = disabled, None = unlimited, int = capped)
    plan_ai_limit = _get_limit(tenant, "ai_daily_limit")

    # Login activity chart — built from LoginEvent history
    from datetime import timedelta
    today = _date.today()

    # Fetch all login events for this tenant (last 365 days is plenty)
    cutoff = datetime.utcnow() - timedelta(days=365)
    events = db.query(LoginEvent).filter(
        LoginEvent.tenant_id == tenant_id,
        LoginEvent.logged_in_at >= cutoff,
    ).all()

    # ── Day: last 30 days ──────────────────────────────────────────────────
    day_keys   = [(today - timedelta(days=29 - i)).isoformat() for i in range(30)]
    day_labels = [(today - timedelta(days=29 - i)).strftime("%d %b") for i in range(30)]
    day_total:  dict = {k: 0 for k in day_keys}
    day_unique: dict = {k: set() for k in day_keys}

    # ── Week: last 12 ISO weeks ────────────────────────────────────────────
    week_keys: list = []
    week_labels: list = []
    week_total:  dict = {}
    week_unique: dict = {}
    for i in range(11, -1, -1):
        d = today - timedelta(weeks=i)
        iso = d.isocalendar()
        k = f"{iso[0]}-W{iso[1]:02d}"
        if k not in week_total:
            week_keys.append(k)
            week_labels.append(f"W{iso[1]} '{str(iso[0])[2:]}")
            week_total[k]  = 0
            week_unique[k] = set()

    # ── Month: last 12 months ──────────────────────────────────────────────
    month_keys: list = []
    month_labels: list = []
    month_total:  dict = {}
    month_unique: dict = {}
    for i in range(11, -1, -1):
        # step back i months from today's month
        m = (today.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        k = m.strftime("%Y-%m")
        if k not in month_total:
            month_keys.append(k)
            month_labels.append(m.strftime("%b %Y"))
            month_total[k]  = 0
            month_unique[k] = set()

    for ev in events:
        d = ev.logged_in_at.date()
        dk = d.isoformat()
        if dk in day_total:
            day_total[dk]  += 1
            day_unique[dk].add(ev.user_id)
        iso = d.isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        if wk in week_total:
            week_total[wk]  += 1
            week_unique[wk].add(ev.user_id)
        mk = d.strftime("%Y-%m")
        if mk in month_total:
            month_total[mk]  += 1
            month_unique[mk].add(ev.user_id)

    login_chart = {
        "day": {
            "labels":       day_labels,
            "total":        [day_total[k]        for k in day_keys],
            "unique":       [len(day_unique[k])  for k in day_keys],
        },
        "week": {
            "labels":       week_labels,
            "total":        [week_total[k]        for k in week_keys],
            "unique":       [len(week_unique[k])  for k in week_keys],
        },
        "month": {
            "labels":       month_labels,
            "total":        [month_total[k]        for k in month_keys],
            "unique":       [len(month_unique[k])  for k in month_keys],
        },
    }

    # ── Gupshup WhatsApp Configuration + Consent & Compliance Log (Sections 8.1-8.4) ──
    callback_url = None
    if tenant.gupshup_webhook_token:
        callback_url = f"https://{OMNIFLOW_PUBLIC_DOMAIN}/webhooks/gupshup/{tenant.gupshup_webhook_token}"
    # Built live (not from the cached whatsapp_opt_in_link column) so it always
    # reflects the current opt-in message template, even for tenants configured
    # before a template wording change.
    opt_in_link = (
        build_opt_in_link(tenant.gupshup_source_number, tenant.name)
        if tenant.gupshup_source_number else None
    )

    consent_filter_employee = request.query_params.get("cf_employee", "")
    consent_filter_type = request.query_params.get("cf_type", "")
    consent_events_q = db.query(WhatsAppConsentEvent).filter(WhatsAppConsentEvent.tenant_id == tenant_id)
    if consent_filter_employee:
        consent_events_q = consent_events_q.filter(WhatsAppConsentEvent.employee_id == consent_filter_employee)
    if consent_filter_type:
        consent_events_q = consent_events_q.filter(WhatsAppConsentEvent.event_type == consent_filter_type)
    consent_events = consent_events_q.order_by(WhatsAppConsentEvent.created_at.desc()).limit(200).all()

    wa_filter_employee = request.query_params.get("wf_employee", "")
    wa_filter_status = request.query_params.get("wf_status", "")
    wa_filter_template = request.query_params.get("wf_template", "")
    wa_logs_q = db.query(WhatsAppMessageLog).filter(WhatsAppMessageLog.tenant_id == tenant_id)
    if wa_filter_employee:
        wa_logs_q = wa_logs_q.filter(WhatsAppMessageLog.recipient_user_id == wa_filter_employee)
    if wa_filter_status:
        wa_logs_q = wa_logs_q.filter(WhatsAppMessageLog.status == wa_filter_status)
    if wa_filter_template:
        wa_logs_q = wa_logs_q.filter(WhatsAppMessageLog.template_name == wa_filter_template)
    wa_logs = wa_logs_q.order_by(WhatsAppMessageLog.created_at.desc()).limit(200).all()
    wa_template_names = sorted({
        row[0] for row in db.query(WhatsAppMessageLog.template_name)
        .filter(WhatsAppMessageLog.tenant_id == tenant_id).distinct().all()
    })

    return templates.TemplateResponse(request, "superadmin/tenant_detail.html", {
        "sa": sa, "tenant": tenant, "stats": stats,
        "users": users, "recent_tickets": recent_tickets,
        "plans": ["STARTER", "PROFESSIONAL", "ENTERPRISE"],
        "pending_count": len(_pending_tenants(db)),
        "industry_names": INDUSTRY_NAMES,
        "label_row": label_row,
        "deployed_items": deployed_items,
        "updates_available": updates_available,
        "fms_flows": fms_flows,
        "fms_flow_limit": fms_flow_limit,
        "fms_flow_count": fms_flow_count,
        "lib_flows": lib_flows,
        "checklist_templates": checklist_templates,
        "fms_tickets": fms_tickets,
        "ai_usage_today": ai_usage_today,
        "ai_usage_month": ai_usage_month,
        "plan_ai_limit": plan_ai_limit,
        "login_chart": login_chart,
        "now": datetime.utcnow(),
        "gupshup_callback_url": callback_url,
        "gupshup_opt_in_link": opt_in_link,
        "consent_events": consent_events,
        "wa_logs": wa_logs,
        "consent_filter_employee": consent_filter_employee,
        "consent_filter_type": consent_filter_type,
        "wa_filter_employee": wa_filter_employee,
        "wa_filter_status": wa_filter_status,
        "wa_filter_template": wa_filter_template,
        "wa_template_names": wa_template_names,
    })


# ── Change Plan ───────────────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/plan")
def sa_change_plan(tenant_id: str, plan: str = Form(...),
                   sa: SuperAdmin = Depends(get_current_sa),
                   db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    if plan not in ("STARTER", "PROFESSIONAL", "ENTERPRISE"):
        raise HTTPException(400, "Invalid plan")
    tenant.plan = plan
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=plan_updated")


# ── Set AI Limit per Tenant ───────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/set-ai-limit")
def sa_reset_ai_limit(tenant_id: str, reset: str = None,
                      sa: SuperAdmin = Depends(get_current_sa),
                      db: Session = Depends(get_db)):
    """GET with ?reset=1 clears the SA override, restoring plan default."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    if reset == "1":
        tenant.ai_custom_limit = None
        db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=ai_limit_updated")


@router.post("/tenants/{tenant_id}/set-ai-limit")
def sa_set_ai_limit(tenant_id: str,
                    ai_limit: str = Form(""),
                    sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    val = ai_limit.strip()
    tenant.ai_custom_limit = int(val) if val else None
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=ai_limit_updated")


# ── Edit Tenant Info ──────────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/edit")
def sa_edit_tenant(tenant_id: str,
                   factory_name: str = Form(...), industry: str = Form(""),
                   contact_name: str = Form(""), contact_email: str = Form(""),
                   fms_label: str = Form(""),
                   sa: SuperAdmin = Depends(get_current_sa),
                   db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    tenant.name          = factory_name
    tenant.industry      = industry or None
    tenant.contact_name  = contact_name or None
    tenant.contact_email = contact_email or None
    # Ensure label row exists
    row = db.query(TenantLabelConfig).filter(
        TenantLabelConfig.tenant_id == tenant_id).first()
    if row is None:
        row = TenantLabelConfig(tenant_id=tenant_id)
        db.add(row)
    # Apply industry preset if selected
    if industry and industry in _PRESETS:
        overrides = _PRESETS[industry]
        def _get(c, i): e = overrides.get(c); return e[i] if e else None
        row.ticket_s=_get("ticket",0);       row.ticket_p=_get("ticket",1)
        row.checklist_s=_get("checklist",0); row.checklist_p=_get("checklist",1)
        row.branch_s=_get("branch",0);       row.branch_p=_get("branch",1)
        row.department_s=_get("department",0);row.department_p=_get("department",1)
        row.employee_s=_get("employee",0);   row.employee_p=_get("employee",1)
        row.industry=industry
    # Always save Flow Board custom label (blank = revert to default "Flow Board")
    row.fms_s = fms_label.strip() or None
    row.fms_p = None
    row.updated_at = datetime.utcnow()
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=updated")


# ── Suspend / Unsuspend ───────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/suspend")
def sa_suspend(tenant_id: str, sa: SuperAdmin = Depends(get_current_sa),
               db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    tenant.is_suspended = True
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=suspended")


@router.post("/tenants/{tenant_id}/unsuspend")
def sa_unsuspend(tenant_id: str, sa: SuperAdmin = Depends(get_current_sa),
                 db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    tenant.is_suspended = False
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=unsuspended")


# ── Gupshup WhatsApp Configuration (Section 8.2) ─────────────────────────────

@router.post("/tenants/{tenant_id}/whatsapp-config")
def sa_save_whatsapp_config(tenant_id: str,
                             gupshup_client_id: str = Form(""),
                             gupshup_secret_token: str = Form(""),
                             gupshup_source_number: str = Form(""),
                             sa: SuperAdmin = Depends(get_current_sa),
                             db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    tenant.gupshup_client_id = gupshup_client_id.strip() or None
    tenant.gupshup_secret_token = gupshup_secret_token.strip() or None
    tenant.gupshup_source_number = gupshup_source_number.strip() or None
    # Auto-generate webhook token/secret the first time credentials are saved —
    # Section 3.3 step 7 / Section 8.2: "you don't invent or type any of these".
    if not tenant.gupshup_webhook_token:
        tenant.gupshup_webhook_token = secrets.token_urlsafe(24)
    if not tenant.gupshup_webhook_secret:
        tenant.gupshup_webhook_secret = secrets.token_urlsafe(32)
    if tenant.gupshup_source_number:
        tenant.whatsapp_opt_in_link = build_opt_in_link(tenant.gupshup_source_number, tenant.name)
    if not tenant.gupshup_waba_status:
        tenant.gupshup_waba_status = "PENDING"
    tenant.whatsapp_config_updated_at = datetime.utcnow()
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=whatsapp_config_saved")


@router.get("/tenants/{tenant_id}/whatsapp-qr.png")
def sa_whatsapp_qr(tenant_id: str, sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    from .services.qr_optin import render_qr_png
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant or not tenant.gupshup_source_number:
        raise HTTPException(404)
    link = build_opt_in_link(tenant.gupshup_source_number, tenant.name)
    png = render_qr_png(link)
    return StreamingResponse(io.BytesIO(png), media_type="image/png")


@router.post("/tenants/{tenant_id}/appeal-package")
def sa_generate_appeal_package(tenant_id: str,
                                start_date: str = Form(""),
                                end_date: str = Form(""),
                                sa: SuperAdmin = Depends(get_current_sa),
                                db: Session = Depends(get_db)):
    """Section 8.6 — Generate Appeal Package. Ships as a CSV/zip bundle
    (no PDF library in the stack — flagged in the handoff summary)."""
    from .services.appeal_package import build_appeal_package
    from datetime import timedelta
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.utcnow()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d") if start_date else (end_dt - timedelta(days=90))
    end_dt = end_dt.replace(hour=23, minute=59, second=59)
    zip_bytes = build_appeal_package(db, tenant, start_dt, end_dt)
    filename = f"appeal_package_{tenant.slug}_{end_dt.date().isoformat()}.zip"
    return StreamingResponse(
        io.BytesIO(zip_bytes), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Delete Tenant (soft) ──────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/delete")
def sa_delete_tenant(tenant_id: str, confirm: str = Form(""),
                     sa: SuperAdmin = Depends(get_current_sa),
                     db: Session = Depends(get_db)):
    if confirm != "DELETE":
        return _redirect(f"/superadmin/tenants/{tenant_id}?err=type_DELETE_to_confirm")
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    # Soft-delete all users, then suspend tenant
    db.query(User).filter(User.tenant_id == tenant_id).update({"is_deleted": True})
    db.query(Ticket).filter(Ticket.tenant_id == tenant_id).update({"is_deleted": True})
    tenant.is_suspended = True
    tenant.name = f"[DELETED] {tenant.name}"
    db.commit()
    return _redirect("/superadmin/tenants?msg=deleted")


# ── Reset Admin Password ──────────────────────────────────────────────────────

@router.get("/approvals", response_class=HTMLResponse)
def sa_approvals(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                 db: Session = Depends(get_db)):
    pending = _pending_tenants(db)
    rows = [(t, _tenant_stats(db, t.id)) for t in pending]
    return templates.TemplateResponse(request, "superadmin/approvals.html",
                                      {"sa": sa, "rows": rows,
                                       "pending_count": len(pending),
                                       "now": datetime.utcnow()})


@router.post("/tenants/{tenant_id}/approve")
def sa_approve(tenant_id: str, plan: str = Form("STARTER"),
               sa: SuperAdmin = Depends(get_current_sa),
               db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    if plan not in ("STARTER", "PROFESSIONAL", "ENTERPRISE"):
        plan = "STARTER"
    tenant.is_approved = True
    tenant.plan = plan
    db.commit()
    return _redirect(f"/superadmin/approvals?msg=approved&name={tenant.name}")


def _send_wa_registration_rejected(phone: str, reason: str, tenant_id: str, db):
    """Pipeline 5C — omniflow_registration_rejected. Sends to prospect phone via
    the platform alert tenant's own Gupshup WABA (see Pipeline 5A in main.py —
    the rejected tenant never gets its own WABA configured). Never raises."""
    from .services.gupshup import send_whatsapp_template, get_platform_tenant
    import json
    import logging
    variables = [reason]
    try:
        platform_tenant = get_platform_tenant(db)
        if not platform_tenant:
            logging.getLogger("superadmin").warning(
                "_send_wa_registration_rejected skipped — no PLATFORM_ALERT_TENANT_ID configured")
            return
        ok, error, *_ = send_whatsapp_template(platform_tenant, phone, "omniflow_registration_rejected", variables)
        db.add(WhatsAppMessageLog(
            tenant_id=tenant_id,
            template_name="omniflow_registration_rejected",
            recipient_user_id=None,
            recipient_phone=phone,
            variables_json=json.dumps(variables),
            status="SENT" if ok else "FAILED",
            error_message=error,
            related_entity_type="registration",
            related_entity_id=tenant_id,
        ))
        db.commit()
    except Exception:
        db.rollback()
        import logging
        logging.getLogger("superadmin").exception("_send_wa_registration_rejected failed for tenant=%s", tenant_id)


@router.post("/tenants/{tenant_id}/reject")
def sa_reject(tenant_id: str, reason: str = Form(""),
              sa: SuperAdmin = Depends(get_current_sa),
              db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    # Fetch prospect phone BEFORE mutating tenant name
    prospect = db.query(User).filter(
        User.tenant_id == tenant.id, User.role == "ADMIN", User.is_deleted == False,
    ).first()
    prospect_phone = prospect.phone if prospect else None
    rejection_reason = reason.strip() or "Your application did not meet our current requirements."
    # Suspend and mark name so it's clearly rejected
    tenant.is_suspended = True
    tenant.name = f"[REJECTED] {tenant.name}"
    db.commit()
    # Pipeline 5C — WhatsApp rejection notice to prospect
    if prospect_phone:
        _send_wa_registration_rejected(prospect_phone, rejection_reason, tenant.id, db)
    return _redirect("/superadmin/approvals?msg=rejected")


@router.post("/tenants/{tenant_id}/reset-password")
def sa_reset_password(tenant_id: str, user_id: str = Form(...),
                      new_password: str = Form(...),
                      sa: SuperAdmin = Depends(get_current_sa),
                      db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id,
                                 User.tenant_id == tenant_id).first()
    if not user:
        raise HTTPException(404)
    user.password_hash = hash_password(new_password)
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=password_reset")


# ── SA Account Management ─────────────────────────────────────────────────────

@router.get("/admins", response_class=HTMLResponse)
def sa_admin_list(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                  db: Session = Depends(get_db)):
    all_admins = db.query(SuperAdmin).order_by(SuperAdmin.created_at).all()
    pending_count = len(_pending_tenants(db))
    return templates.TemplateResponse(request, "superadmin/admins.html", {
        "sa": sa, "all_admins": all_admins,
        "pending_count": pending_count,
        "now": datetime.utcnow(),
    })


@router.post("/admins/new")
def sa_add_admin(name: str = Form(...), email: str = Form(...),
                 password: str = Form(...),
                 sa: SuperAdmin = Depends(get_current_sa),
                 db: Session = Depends(get_db)):
    if db.query(SuperAdmin).filter(SuperAdmin.email == email).first():
        return _redirect("/superadmin/admins?err=email_taken")
    new_sa = SuperAdmin(name=name, email=email, password_hash=sa_hash(password))
    db.add(new_sa)
    db.commit()
    return _redirect("/superadmin/admins?msg=created")


@router.post("/admins/{sa_id}/deactivate")
def sa_deactivate_admin(sa_id: str, sa: SuperAdmin = Depends(get_current_sa),
                        db: Session = Depends(get_db)):
    if sa_id == sa.id:
        return _redirect("/superadmin/admins?err=cannot_deactivate_self")
    target = db.query(SuperAdmin).filter(SuperAdmin.id == sa_id).first()
    if not target:
        raise HTTPException(404)
    target.is_active = False
    db.commit()
    return _redirect("/superadmin/admins?msg=deactivated")


@router.post("/admins/{sa_id}/activate")
def sa_activate_admin(sa_id: str, sa: SuperAdmin = Depends(get_current_sa),
                      db: Session = Depends(get_db)):
    target = db.query(SuperAdmin).filter(SuperAdmin.id == sa_id).first()
    if not target:
        raise HTTPException(404)
    target.is_active = True
    db.commit()
    return _redirect("/superadmin/admins?msg=activated")


# ── SA Profile (change own name / password) ───────────────────────────────────

@router.get("/profile", response_class=HTMLResponse)
def sa_profile_page(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    pending_count = len(_pending_tenants(db))
    return templates.TemplateResponse(request, "superadmin/profile.html", {
        "sa": sa, "pending_count": pending_count,
        "now": datetime.utcnow(),
    })


@router.post("/profile/name")
def sa_update_name(name: str = Form(...),
                   sa: SuperAdmin = Depends(get_current_sa),
                   db: Session = Depends(get_db)):
    sa.name = name
    db.commit()
    return _redirect("/superadmin/profile?msg=name_updated")


@router.post("/profile/password")
def sa_update_password(current_password: str = Form(...),
                       new_password: str = Form(...),
                       confirm_password: str = Form(...),
                       sa: SuperAdmin = Depends(get_current_sa),
                       db: Session = Depends(get_db)):
    if not sa_verify(current_password, sa.password_hash):
        return _redirect("/superadmin/profile?err=wrong_current")
    if new_password != confirm_password:
        return _redirect("/superadmin/profile?err=mismatch")
    if len(new_password) < 6:
        return _redirect("/superadmin/profile?err=too_short")
    sa.password_hash = sa_hash(new_password)
    db.commit()
    # Force re-login with new password
    resp = _redirect("/superadmin/login?msg=password_changed")
    resp.delete_cookie(COOKIE)
    return resp


# ── Feature Flag Overrides — Phase 0-I ───────────────────────────────────────

@router.get("/tenants/{tenant_id}/features", response_class=HTMLResponse)
def sa_tenant_features(request: Request, tenant_id: str,
                       sa: SuperAdmin = Depends(get_current_sa),
                       db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)

    overrides = {
        o.feature: o
        for o in db.query(TenantFeatureOverride).filter(
            TenantFeatureOverride.tenant_id == tenant_id).all()
    }

    # Build feature rows grouped by category
    from collections import defaultdict
    by_category = defaultdict(list)
    for fname, (label, category, min_plan) in FEATURE_CATALOG.items():
        plan_allows = get_plan_features(tenant.plan or "STARTER").get(fname, False)
        override    = overrides.get(fname)
        effective   = override.enabled if override else plan_allows
        by_category[category].append({
            "name": fname, "label": label, "min_plan": min_plan,
            "plan_allows": plan_allows, "override": override,
            "effective": effective,
        })

    pending_count = len(_pending_tenants(db))
    return templates.TemplateResponse(request, "superadmin/tenant_features.html", {
        "sa": sa, "tenant": tenant,
        "by_category": dict(by_category),
        "plan_labels": PLAN_LABELS,
        "pending_count": pending_count,
        "now": datetime.utcnow(),
    })


@router.post("/tenants/{tenant_id}/features/override")
def sa_set_override(tenant_id: str,
                    feature: str = Form(...),
                    action: str = Form(...),   # "enable", "disable", "clear"
                    note: str = Form(""),
                    sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404)
    if feature not in FEATURE_CATALOG:
        raise HTTPException(400, "Unknown feature")

    existing = db.query(TenantFeatureOverride).filter(
        TenantFeatureOverride.tenant_id == tenant_id,
        TenantFeatureOverride.feature   == feature,
    ).first()

    if action == "clear":
        if existing:
            db.delete(existing)
    else:
        enabled = (action == "enable")
        if existing:
            existing.enabled = enabled
            existing.note    = note or None
        else:
            db.add(TenantFeatureOverride(
                tenant_id=tenant_id, feature=feature,
                enabled=enabled, note=note or None,
            ))
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}/features?msg=saved")


@router.post("/tenants/{tenant_id}/features/clear-all")
def sa_clear_all_overrides(tenant_id: str,
                            sa: SuperAdmin = Depends(get_current_sa),
                            db: Session = Depends(get_db)):
    db.query(TenantFeatureOverride).filter(
        TenantFeatureOverride.tenant_id == tenant_id
    ).delete()
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}/features?msg=cleared")


# ── Plan Upgrade Requests ──────────────────────────────────────────────────────

@router.post("/upgrade-requests/{req_id}/action")
def sa_action_upgrade_request(
    req_id: str,
    action: str = Form(...),   # "actioned" or "dismissed"
    sa: SuperAdmin = Depends(get_current_sa),
    db: Session = Depends(get_db),
):
    req = db.query(PlanUpgradeRequest).filter(PlanUpgradeRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    req.status = "ACTIONED" if action == "actioned" else "DISMISSED"
    req.actioned_at = datetime.utcnow()
    req.actioned_by = sa.id
    # If actioning: upgrade the tenant's plan
    if action == "actioned":
        tenant = db.query(Tenant).get(req.tenant_id)
        if tenant:
            tenant.plan = req.to_plan
    db.commit()
    return _redirect("/superadmin/dashboard?msg=upgrade_actioned")


# ── Deploy FMS Flow to Tenant ─────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/deploy-flow")
def sa_deploy_flow(
    tenant_id: str,
    library_flow_id: str = Form(...),
    notes: str = Form(""),
    sa: SuperAdmin = Depends(get_current_sa),
    db: Session = Depends(get_db),
):
    """SA deploys a library flow template to a specific tenant.
    Enforces plan flow limit before deploying."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    # Plan limit check
    plan = tenant.plan or "STARTER"
    max_flows = PLAN_LIMITS.get(plan, {}).get("max_fms_flows")
    current_count = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tenant_id,
        FMSFlow.is_deleted == False,
    ).count()
    if max_flows is not None and current_count >= max_flows:
        return _redirect(
            f"/superadmin/tenants/{tenant_id}"
            f"?err=Flow+limit+reached+for+{plan}+plan+({max_flows}+flows).+Upgrade+their+plan+first."
        )

    lib_flow = db.query(LibraryFlowTemplate).get(library_flow_id)
    if not lib_flow:
        raise HTTPException(404, "Library flow not found")

    # Create tenant FMS flow from library template
    from .database import FMSStage as _Stage
    flow = FMSFlow(
        tenant_id=tenant_id,
        name=lib_flow.name,
        description=lib_flow.description,
        color=getattr(lib_flow, "color", "#3b82f6"),
        is_active=True,
        library_flow_id=library_flow_id,
        library_version_at_deploy=lib_flow.version,
        created_by_id=None,
    )
    db.add(flow)
    db.flush()

    for lib_stage in (lib_flow.stages or []):
        db.add(_Stage(
            flow_id=flow.id,
            tenant_id=tenant_id,
            name=lib_stage.name,
            description=getattr(lib_stage, "description", None),
            order=lib_stage.order or 0,
            color=getattr(lib_stage, "color", "#3b82f6"),
            is_terminal=getattr(lib_stage, "is_terminal", False),
            target_tat_hours=getattr(lib_stage, "target_tat_hours", None),
            is_mandatory=getattr(lib_stage, "is_mandatory", True),
            completion_note_required=getattr(lib_stage, "completion_note_required", False),
            evidence_required=getattr(lib_stage, "evidence_required", False),
            custom_fields_json=getattr(lib_stage, "custom_fields_json", "[]") or "[]",
            sub_module_tag=getattr(lib_stage, "sub_module_tag", None),
        ))

    # Record deployment in tenant_deployed_items
    db.add(TenantDeployedItem(
        tenant_id=tenant_id,
        item_type="flow",
        library_item_id=library_flow_id,
        item_name=lib_flow.name,
        deployed_version=lib_flow.version,
        deployed_by=sa.id,
        notes=notes or None,
    ))
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=flow_deployed")


# ── Sync deployed flow stages from library ────────────────────────────────────

@router.post("/tenants/{tenant_id}/sync-flow/{flow_id}")
def sa_sync_flow(
    tenant_id: str, flow_id: str,
    sa: SuperAdmin = Depends(get_current_sa),
    db: Session = Depends(get_db),
):
    """Sync stage settings (custom fields, TAT, options) from current library version
    into an already-deployed tenant flow. Matches stages by order position.
    Never touches stage names, ticket data, or assignments."""
    import json as _json
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id, FMSFlow.tenant_id == tenant_id,
    ).first()
    if not flow or not flow.library_flow_id:
        raise HTTPException(404, "Flow not found or not library-sourced")

    lib_flow = db.query(LibraryFlowTemplate).get(flow.library_flow_id)
    if not lib_flow:
        raise HTTPException(404, "Library template not found")

    # Build lookup: order → library stage
    lib_stages_by_order = {
        s.order: s for s in (lib_flow.stages or [])
    }

    tenant_stages = sorted(
        [s for s in (flow.stages or []) if not s.is_deleted],
        key=lambda s: s.order,
    )

    synced = 0
    for ts in tenant_stages:
        ls = lib_stages_by_order.get(ts.order)
        if not ls:
            continue
        ts.custom_fields_json = getattr(ls, "custom_fields_json", "[]") or "[]"
        ts.sub_module_tag = getattr(ls, "sub_module_tag", None)
        ts.color = getattr(ls, "color", "#3b82f6") or "#3b82f6"
        ts.target_tat_hours = getattr(ls, "target_tat_hours", None)
        ts.completion_note_required = getattr(ls, "completion_note_required", False)
        ts.evidence_required = getattr(ls, "evidence_required", False)
        ts.description = getattr(ls, "description", None) or ts.description
        synced += 1

    flow.library_version_at_deploy = lib_flow.version
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=flow_synced&synced={synced}")


# ── Undeploy a library item (label bundle or flow) ────────────────────────────

@router.post("/tenants/{tenant_id}/undeploy-item/{item_id}")
def sa_undeploy_item(
    tenant_id: str, item_id: str,
    sa: SuperAdmin = Depends(get_current_sa),
    db: Session = Depends(get_db),
):
    record = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.id == item_id,
        TenantDeployedItem.tenant_id == tenant_id,
    ).first()
    if not record:
        raise HTTPException(404, "Deployed item not found")

    # If it was a flow deployment, also soft-delete the FMSFlow
    if record.item_type == "flow":
        flow = db.query(FMSFlow).filter(
            FMSFlow.tenant_id == tenant_id,
            FMSFlow.library_flow_id == record.library_item_id,
            FMSFlow.is_deleted == False,
        ).first()
        if flow:
            flow.is_deleted = True

    db.delete(record)
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=item_undeployed")


# ── Delete (soft) an FMS flow from a tenant ───────────────────────────────────

@router.post("/tenants/{tenant_id}/toggle-flow/{flow_id}")
def sa_toggle_flow(
    tenant_id: str, flow_id: str,
    sa: SuperAdmin = Depends(get_current_sa),
    db: Session = Depends(get_db),
):
    """Activate / deactivate a deployed flow without deleting it."""
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id,
        FMSFlow.tenant_id == tenant_id,
    ).first()
    if not flow:
        raise HTTPException(404, "Flow not found")
    flow.is_active = not flow.is_active
    db.commit()
    action = "flow_activated" if flow.is_active else "flow_deactivated"
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg={action}")


@router.post("/tenants/{tenant_id}/delete-flow/{flow_id}")
def sa_delete_flow(
    tenant_id: str, flow_id: str,
    sa: SuperAdmin = Depends(get_current_sa),
    db: Session = Depends(get_db),
):
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id,
        FMSFlow.tenant_id == tenant_id,
        FMSFlow.is_deleted == False,
    ).first()
    if not flow:
        raise HTTPException(404, "Flow not found")
    flow.is_deleted = True
    # Also remove tracking record if exists
    tracking = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.tenant_id == tenant_id,
        TenantDeployedItem.item_type == "flow",
        TenantDeployedItem.library_item_id == flow.library_flow_id,
    ).first()
    if tracking:
        db.delete(tracking)
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=flow_deleted")


@router.post("/tenants/{tenant_id}/hard-delete-flow/{flow_id}")
def sa_hard_delete_flow(
    tenant_id: str, flow_id: str,
    sa: SuperAdmin = Depends(get_current_sa),
    db: Session = Depends(get_db),
):
    """Permanently remove a flow and all its stage/ticket data from the database."""
    from .database import FMSStage, FMSStageHistory, FMSTicket, FMSEvent
    flow = db.query(FMSFlow).filter(
        FMSFlow.id == flow_id,
        FMSFlow.tenant_id == tenant_id,
    ).first()
    if not flow:
        raise HTTPException(404, "Flow not found")

    # Delete all related data in dependency order
    ticket_ids = [t.id for t in db.query(FMSTicket.id).filter(FMSTicket.flow_id == flow_id).all()]
    if ticket_ids:
        db.query(FMSStageHistory).filter(FMSStageHistory.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)
        db.query(FMSEvent).filter(FMSEvent.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)
        db.query(FMSTicket).filter(FMSTicket.flow_id == flow_id).delete(synchronize_session=False)
    db.query(FMSStage).filter(FMSStage.flow_id == flow_id).delete(synchronize_session=False)

    # Remove deployment tracking record if present
    tracking = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.tenant_id == tenant_id,
        TenantDeployedItem.item_type == "flow",
        TenantDeployedItem.library_item_id == flow.library_flow_id,
    ).first()
    if tracking:
        db.delete(tracking)

    db.delete(flow)
    db.commit()
    return _redirect(f"/superadmin/tenants/{tenant_id}?msg=flow_permanently_deleted")