"""Setup — /api/v1/setup. Native-app counterpart of the website's Setup
list (app/main.py '/setup') and Setup > Notifications page. Read-heavy
overview + the one sub-page the design wires up end-to-end (Notifications):
office hours, TaT alert thresholds, checklist reminder hours and WhatsApp
per-event toggles all live on the same Tenant columns the website's
/setup/notifications route writes, and that the scheduler/notification
pipelines already read (see app/scheduler.py, app/notifications.py) — so
saving here takes effect everywhere those columns are consulted, exactly
like saving on the website does. No new config surface, just a mobile
client for the existing one.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..constants import LIMIT_LABELS, PLAN_LIMITS, get_limit
from ..database import (
    AttendanceRule,
    Branch,
    ChecklistTemplate,
    CustomReferenceList,
    Customer,
    Department,
    EndProduct,
    FMSFlow,
    PerformanceFormula,
    RawMaterial,
    Tenant,
    UnitOfMeasure,
    User,
    Vendor,
    get_db,
)
from .security import get_current_api_user

router = APIRouter(prefix="/setup", tags=["Setup"])


def _require_admin_or_pm(user: User = Depends(get_current_api_user)) -> User:
    if user.role not in ("ADMIN", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Admin or Product Manager only")
    return user


# ── Overview (Setup list) ──────────────────────────────────────────────────

class PlanUsageOut(BaseModel):
    label: str
    used: int
    limit: int | None


class SetupRowOut(BaseModel):
    key: str
    icon: str
    label: str
    sub: str


class SetupSectionOut(BaseModel):
    title: str
    rows: list[SetupRowOut]


class SetupOverviewOut(BaseModel):
    tenant_name: str
    plan: str
    plan_usage: list[PlanUsageOut]
    sections: list[SetupSectionOut]


@router.get("/overview", response_model=SetupOverviewOut)
def setup_overview(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()

    def count(model, **filters):
        q = db.query(model).filter(model.tenant_id == user.tenant_id)
        for k, v in filters.items():
            q = q.filter(getattr(model, k) == v)
        return q.count()

    branches = count(Branch)
    departments = count(Department)
    employees = db.query(User).filter(User.tenant_id == user.tenant_id, User.is_deleted == False).count()
    customers = count(Customer)
    products = count(EndProduct)
    vendors = count(Vendor)
    materials = count(RawMaterial)
    lists = count(CustomReferenceList)
    uoms = count(UnitOfMeasure)
    flows = db.query(FMSFlow).filter(FMSFlow.tenant_id == user.tenant_id, FMSFlow.is_active == True).count()
    day_status_rules = count(AttendanceRule)
    formula = (
        db.query(PerformanceFormula)
        .filter(PerformanceFormula.tenant_id == user.tenant_id, PerformanceFormula.is_active == True)
        .first()
    )

    plan_usage = [
        PlanUsageOut(label=LIMIT_LABELS["max_users"], used=employees, limit=get_limit(tenant, "max_users")),
        PlanUsageOut(label=LIMIT_LABELS["max_branches"], used=branches, limit=get_limit(tenant, "max_branches")),
        PlanUsageOut(label=LIMIT_LABELS["max_fms_flows"], used=flows, limit=get_limit(tenant, "max_fms_flows")),
        PlanUsageOut(
            label=LIMIT_LABELS["max_checklist_templates"],
            used=count(ChecklistTemplate),
            limit=get_limit(tenant, "max_checklist_templates"),
        ),
    ]

    sections = [
        SetupSectionOut(title="Organisation", rows=[
            SetupRowOut(key="branches", icon="🏭", label="Branches & Departments", sub=f"{branches} branches · {departments} departments"),
            SetupRowOut(key="employees", icon="👥", label="Employees", sub=f"{employees} team members"),
        ]),
        SetupSectionOut(title="Reference Data", rows=[
            SetupRowOut(key="customers", icon="🤝", label="Customers", sub=f"{customers} accounts"),
            SetupRowOut(key="products", icon="📦", label="End Products", sub=f"{products} SKUs"),
            SetupRowOut(key="vendors", icon="🏭", label="Vendors", sub=f"{vendors} vendors"),
            SetupRowOut(key="materials", icon="🧱", label="Raw Materials", sub=f"{materials} materials"),
            SetupRowOut(key="lists", icon="📋", label="Custom Lists", sub=f"{lists} lists"),
            SetupRowOut(key="uom", icon="📏", label="Units of Measure", sub=f"{uoms} units"),
        ]),
        SetupSectionOut(title="FMS", rows=[
            # Building/editing stages, routing & custom fields is web-only; the
            # app can only view flows and flip Active/Inactive — see
            # api_v1/setup_config.py's flows section and FlowsScreen.tsx.
            SetupRowOut(key="flows", icon="🔀", label="Flows", sub=f"{flows} active flows · edit on web"),
        ]),
        SetupSectionOut(title="Sales", rows=[
            SetupRowOut(key="pricing", icon="💰", label="Pricing & Margins", sub="Managed on the website"),
        ]),
        SetupSectionOut(title="Configuration", rows=[
            SetupRowOut(
                key="notifications", icon="🔔", label="Notifications",
                sub=f"Office hours {tenant.work_start_time}–{tenant.work_end_time} · TaT alerts {tenant.ticket_notif_tat_pct}/{tenant.ticket_notif_tat_pct_both}%",
            ),
            SetupRowOut(key="performance", icon="📐", label="Performance", sub=(formula.label if formula and formula.label else "Default formula")),
            SetupRowOut(key="day_status", icon="🗓️", label="Day-Status Rules", sub=f"{day_status_rules} rules active"),
        ]),
        SetupSectionOut(title="Guide", rows=[
            SetupRowOut(key="how_to", icon="📖", label="How To", sub="Setup walkthrough"),
        ]),
    ]

    return SetupOverviewOut(tenant_name=tenant.name, plan=tenant.plan, plan_usage=plan_usage, sections=sections)


# ── Notifications ───────────────────────────────────────────────────────────

class NotificationSettingsOut(BaseModel):
    work_start_time: str
    work_end_time: str
    work_days: list[int]
    suppress_notif_outside_hours: bool
    ticket_notif_tat_pct: int
    ticket_notif_tat_pct_both: int
    checklist_notif_hours: list[int]


def _to_settings_out(tenant: Tenant) -> NotificationSettingsOut:
    days = [int(d) for d in (tenant.work_days or "0,1,2,3,4").split(",") if d.strip().isdigit()]
    hours = [int(h) for h in (tenant.checklist_notif_hours or "8,13,18").split(",") if h.strip().isdigit()]
    return NotificationSettingsOut(
        work_start_time=tenant.work_start_time or "09:00",
        work_end_time=tenant.work_end_time or "18:00",
        work_days=days,
        suppress_notif_outside_hours=bool(tenant.suppress_notif_outside_hours),
        ticket_notif_tat_pct=tenant.ticket_notif_tat_pct or 80,
        ticket_notif_tat_pct_both=tenant.ticket_notif_tat_pct_both or 90,
        checklist_notif_hours=hours,
    )


@router.get("/notifications", response_model=NotificationSettingsOut)
def get_notification_settings(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return _to_settings_out(tenant)


class NotificationSettingsIn(BaseModel):
    suppress_notif_outside_hours: bool


@router.put("/notifications", response_model=NotificationSettingsOut)
def update_notification_settings(
    payload: NotificationSettingsIn,
    user: User = Depends(_require_admin_or_pm),
    db: Session = Depends(get_db),
):
    """Saves the office-hours suppression switch. Every per-event channel
    toggle (in-app/push/WhatsApp, for checklists/FMS/delegations) now lives
    exclusively in the notification-rules table below — GET/PUT
    /setup/notification-rules — not here, avoiding two knobs for the same
    setting. Office hours themselves, TaT thresholds and checklist reminder
    hours stay display-only here (edited on the website)."""
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    tenant.suppress_notif_outside_hours = payload.suppress_notif_outside_hours
    db.commit()
    db.refresh(tenant)
    return _to_settings_out(tenant)


# ── Notification rules — Setup > Notifications toggle table ────────────────
# One row per condition (app/notification_rules.py registry); each has
# independent In-App/Push/WhatsApp toggles, highly customizable per tenant.
# Same registry/gate helper the desktop /setup/notifications page and every
# notify_*/scheduler job now read — saving here takes effect everywhere.

class NotificationConditionOut(BaseModel):
    key: str
    category: str
    label: str
    cadence: str
    recipients: str


class NotificationRuleOut(BaseModel):
    condition_key: str
    in_app: bool
    push: bool
    whatsapp: bool
    recipients: list[str]


class NotificationRulesOut(BaseModel):
    conditions: list[NotificationConditionOut]
    rules: list[NotificationRuleOut]
    available_roles: list[str]
    role_labels: dict


@router.get("/notification-rules", response_model=NotificationRulesOut)
def get_notification_rules(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    from ..notification_rules import REGISTRY, channel_enabled, get_recipient_roles, AVAILABLE_ROLES, ROLE_LABELS
    conditions = [
        NotificationConditionOut(key=c.key, category=c.category, label=c.label, cadence=c.cadence, recipients=c.recipients)
        for c in REGISTRY
    ]
    rules = [
        NotificationRuleOut(
            condition_key=c.key,
            in_app=channel_enabled(db, user.tenant_id, c.key, "in_app"),
            push=channel_enabled(db, user.tenant_id, c.key, "push"),
            whatsapp=channel_enabled(db, user.tenant_id, c.key, "whatsapp"),
            recipients=sorted(get_recipient_roles(db, user.tenant_id, c.key)),
        )
        for c in REGISTRY
    ]
    return NotificationRulesOut(conditions=conditions, rules=rules, available_roles=AVAILABLE_ROLES, role_labels=ROLE_LABELS)


class NotificationRuleIn(BaseModel):
    condition_key: str
    in_app: bool
    push: bool
    whatsapp: bool
    recipients: list[str] = []


@router.put("/notification-rules", response_model=NotificationRulesOut)
def update_notification_rules(
    payload: list[NotificationRuleIn],
    user: User = Depends(_require_admin_or_pm),
    db: Session = Depends(get_db),
):
    from ..notification_rules import get_condition, get_or_seed_rule, AVAILABLE_ROLES
    import json as _json
    for item in payload:
        if not get_condition(item.condition_key):
            continue  # ignore unknown keys rather than 400 — forward compatible
        rule = get_or_seed_rule(db, user.tenant_id, item.condition_key)
        rule.in_app_enabled = item.in_app
        rule.push_enabled = item.push
        rule.whatsapp_enabled = item.whatsapp
        rule.recipients_json = _json.dumps([r for r in item.recipients if r in AVAILABLE_ROLES])
    db.commit()
    return get_notification_rules(user, db)
