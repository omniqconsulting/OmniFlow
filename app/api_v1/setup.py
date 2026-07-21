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
    wa_notif_ticket_assigned: bool
    wa_notif_ticket_escalated: bool
    wa_notif_fms_ticket_created: bool


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
        wa_notif_ticket_assigned=bool(tenant.wa_notif_ticket_assigned),
        wa_notif_ticket_escalated=bool(tenant.wa_notif_ticket_escalated),
        wa_notif_fms_ticket_created=bool(tenant.wa_notif_fms_ticket_created),
    )


@router.get("/notifications", response_model=NotificationSettingsOut)
def get_notification_settings(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return _to_settings_out(tenant)


class NotificationSettingsIn(BaseModel):
    suppress_notif_outside_hours: bool
    wa_notif_ticket_assigned: bool
    wa_notif_ticket_escalated: bool
    wa_notif_fms_ticket_created: bool


@router.put("/notifications", response_model=NotificationSettingsOut)
def update_notification_settings(
    payload: NotificationSettingsIn,
    user: User = Depends(_require_admin_or_pm),
    db: Session = Depends(get_db),
):
    """Saves the subset of Setup > Notifications the app's design exposes
    (WhatsApp per-event toggles + the office-hours suppression switch).
    Office hours themselves, TaT thresholds and checklist reminder hours are
    display-only here (same source of truth as the website, edited there) —
    matching the design, which renders them read-only on this screen."""
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    tenant.suppress_notif_outside_hours = payload.suppress_notif_outside_hours
    tenant.wa_notif_ticket_assigned = payload.wa_notif_ticket_assigned
    tenant.wa_notif_ticket_escalated = payload.wa_notif_ticket_escalated
    tenant.wa_notif_fms_ticket_created = payload.wa_notif_fms_ticket_created
    db.commit()
    db.refresh(tenant)
    return _to_settings_out(tenant)
