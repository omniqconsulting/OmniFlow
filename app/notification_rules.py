"""Notification condition registry — single source of truth for the Setup >
Notifications toggle table (both desktop and native) and for every
notify_*/send_whatsapp_for_* call site's channel gating.

A tenant's actual preference lives in the NotificationRule table (one row
per tenant per condition_key); a MISSING row means "use this registry's
default", not "disabled" — so a tenant that never opens Setup keeps sane
defaults rather than silently going quiet.
"""
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .database import Branch, LeaveRequest, NotificationRule, User

# Every recipient-role option offered in the Setup > Notifications multi-select,
# uniformly for every condition — a role that doesn't apply to a given event
# (e.g. "Helper" on a checklist notification, which has no helpers) simply
# resolves to an empty candidate pool rather than being hidden from the UI.
AVAILABLE_ROLES = ["ADMIN", "MANAGER", "ASSIGNEE", "HELPER"]
ROLE_LABELS = {"ADMIN": "Admin", "MANAGER": "Manager", "ASSIGNEE": "Assignee", "HELPER": "Helper"}


@dataclass(frozen=True)
class NotificationCondition:
    key: str
    category: str  # "Checklist" | "FMS" | "Delegation"
    label: str
    cadence: str
    default_recipients: tuple  # subset of AVAILABLE_ROLES
    default_in_app: bool
    default_push: bool
    default_whatsapp: bool
    # Legacy Tenant.wa_notif_* column this condition's WhatsApp toggle was
    # seeded from the first time a tenant's row is materialized (None if
    # there was no prior single-purpose column for it).
    legacy_wa_column: Optional[str] = None

    @property
    def recipients(self) -> str:
        """Human-readable default recipients string, e.g. 'Admin, Manager'."""
        return ", ".join(ROLE_LABELS[r] for r in self.default_recipients)


REGISTRY = [
    NotificationCondition(
        "checklist_assigned", "Checklist", "A checklist is assigned to you",
        "Immediately", ("ASSIGNEE",), True, True, True, None,
    ),
    NotificationCondition(
        "checklist_completed", "Checklist", "A team member completes a checklist",
        "Immediately", ("MANAGER",), True, True, False, None,
    ),
    NotificationCondition(
        "checklist_reminder", "Checklist", "Reminder for checklists due today",
        "Once a day at the configured hour(s); skips the assignee's leave/branch off-days, deferred to their next working day",
        ("ASSIGNEE",), True, True, False, None,
    ),
    NotificationCondition(
        "fms_ticket_created", "FMS", "A new flow ticket is opened",
        "Immediately", ("ADMIN", "MANAGER", "ASSIGNEE"), True, True, True, "wa_notif_fms_ticket_created",
    ),
    NotificationCondition(
        "fms_stage_forward", "FMS", "Ticket moves to the next stage",
        "Immediately", ("ADMIN", "MANAGER", "ASSIGNEE"), True, True, False, "wa_notif_fms_stage_transition",
    ),
    NotificationCondition(
        "fms_stage_backward", "FMS", "Ticket is sent back a stage",
        "Immediately", ("ADMIN", "MANAGER", "ASSIGNEE"), True, True, False, None,
    ),
    NotificationCondition(
        "fms_flagged", "FMS", "Ticket is flagged",
        "Immediately", ("ADMIN", "MANAGER"), True, True, True, "wa_notif_fms_ticket_flagged",
    ),
    NotificationCondition(
        "fms_closed", "FMS", "Ticket is closed",
        "Immediately", ("ADMIN", "MANAGER"), False, False, True, "wa_notif_fms_ticket_closed",
    ),
    NotificationCondition(
        "fms_help_needed", "FMS", "Assignee requests help",
        "Immediately", ("ADMIN", "MANAGER"), True, True, True, None,
    ),
    NotificationCondition(
        "fms_tat_breach", "FMS", "Stage is over its target turnaround time",
        "Checked every 30 min, business-hours + leave/branch aware, one alert per breach",
        ("ADMIN", "MANAGER", "ASSIGNEE"), True, True, False, None,
    ),
    NotificationCondition(
        "ticket_assigned", "Delegation", "A ticket is (re)assigned to you",
        "Immediately", ("ASSIGNEE",), True, True, True, "wa_notif_ticket_assigned",
    ),
    NotificationCondition(
        "ticket_status_change", "Delegation", "Ticket status changes (ack / in-progress)",
        "Immediately", ("MANAGER",), True, True, False, None,
    ),
    NotificationCondition(
        "ticket_closed", "Delegation", "Ticket is closed",
        "Immediately", ("ADMIN", "MANAGER"), False, False, True, "wa_notif_ticket_closed",
    ),
    NotificationCondition(
        "ticket_comment", "Delegation", "Someone comments on a ticket",
        "Immediately", ("ADMIN", "MANAGER", "ASSIGNEE"), True, True, False, None,
    ),
    NotificationCondition(
        "ticket_flagged", "Delegation", "Ticket is flagged/escalated",
        "Immediately", ("ADMIN", "MANAGER", "ASSIGNEE"), True, True, True, "wa_notif_ticket_escalated",
    ),
    NotificationCondition(
        "ticket_help_requested", "Delegation", "Assignee requests help",
        "Immediately", ("ADMIN", "MANAGER"), True, True, True, None,
    ),
    NotificationCondition(
        "ticket_helper_added", "Delegation", "You're added as a helper on a ticket",
        "Immediately", ("HELPER",), True, True, False, None,
    ),
    NotificationCondition(
        "ticket_unacknowledged", "Delegation", "High/Critical ticket unacknowledged 2h+",
        "Once per ticket, never repeats", ("ADMIN", "MANAGER", "ASSIGNEE"), True, True, False, None,
    ),
    NotificationCondition(
        "ticket_morning_summary", "Delegation", "Daily overdue/due-today digest",
        "Once a day, morning IST", ("ASSIGNEE",), True, True, True, None,
    ),
    NotificationCondition(
        "ticket_tat_reminder", "Delegation", "Ticket approaching/over target TAT",
        "Every 30 min, business-hours + leave/branch aware",
        ("ADMIN", "MANAGER", "ASSIGNEE"), True, True, True, "wa_notif_ticket_tat_reminder",
    ),
]

_BY_KEY = {c.key: c for c in REGISTRY}


def get_condition(key: str) -> Optional[NotificationCondition]:
    return _BY_KEY.get(key)


def _seed_rule(db: Session, tenant_id: str, cond: NotificationCondition) -> NotificationRule:
    whatsapp_default = cond.default_whatsapp
    if cond.legacy_wa_column:
        from .database import Tenant
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        legacy_val = getattr(tenant, cond.legacy_wa_column, None) if tenant else None
        if legacy_val is not None:
            whatsapp_default = bool(legacy_val)
    rule = NotificationRule(
        tenant_id=tenant_id, condition_key=cond.key,
        in_app_enabled=cond.default_in_app, push_enabled=cond.default_push,
        whatsapp_enabled=whatsapp_default,
        recipients_json=json.dumps(list(cond.default_recipients)),
    )
    db.add(rule)
    db.flush()
    return rule


def get_or_seed_rule(db: Session, tenant_id: str, condition_key: str) -> Optional[NotificationRule]:
    """Returns the tenant's persisted rule, creating+seeding it from the
    registry/legacy column on first access. None if condition_key is unknown."""
    cond = get_condition(condition_key)
    if not cond:
        return None
    rule = db.query(NotificationRule).filter(
        NotificationRule.tenant_id == tenant_id, NotificationRule.condition_key == condition_key,
    ).first()
    if rule:
        return rule
    return _seed_rule(db, tenant_id, cond)


def channel_enabled(db: Session, tenant_id: str, condition_key: str, channel: str) -> bool:
    """channel: 'in_app' | 'push' | 'whatsapp'. Missing row -> registry default
    (does NOT write a row — read-only check, keeps hot notification paths cheap)."""
    cond = get_condition(condition_key)
    if not cond:
        return True  # unknown key — fail open rather than silently swallow a notification
    rule = db.query(NotificationRule).filter(
        NotificationRule.tenant_id == tenant_id, NotificationRule.condition_key == condition_key,
    ).first()
    if rule is None:
        return {"in_app": cond.default_in_app, "push": cond.default_push, "whatsapp": cond.default_whatsapp}[channel]
    return {"in_app": rule.in_app_enabled, "push": rule.push_enabled, "whatsapp": rule.whatsapp_enabled}[channel]


def get_recipient_roles(db: Session, tenant_id: str, condition_key: str) -> set:
    """The tenant's configured recipient roles for this condition (subset of
    AVAILABLE_ROLES) — missing row or unparsable JSON falls back to the
    registry default, same convention as channel_enabled()."""
    cond = get_condition(condition_key)
    if not cond:
        return set(AVAILABLE_ROLES)  # unknown key — fail open
    rule = db.query(NotificationRule).filter(
        NotificationRule.tenant_id == tenant_id, NotificationRule.condition_key == condition_key,
    ).first()
    if rule is None or rule.recipients_json is None:
        return set(cond.default_recipients)
    try:
        return {r for r in json.loads(rule.recipients_json) if r in AVAILABLE_ROLES}
    except Exception:
        return set(cond.default_recipients)


def filter_recipients(db: Session, tenant_id: str, condition_key: str, *,
                       admin_ids=None, manager_ids=None, assignee_id=None,
                       helper_ids=None, actor_id=None) -> list:
    """Build the actual notification audience for `condition_key` from the
    candidate pools for each role, keeping only the roles the tenant has
    configured as recipients — then always drop the actor, regardless of
    whether their role is checked, so nobody is ever notified of their own
    action."""
    roles = get_recipient_roles(db, tenant_id, condition_key)
    audience = set()
    if "ADMIN" in roles:
        audience.update(admin_ids or [])
    if "MANAGER" in roles:
        audience.update(manager_ids or [])
    if "ASSIGNEE" in roles and assignee_id:
        audience.add(assignee_id)
    if "HELPER" in roles:
        audience.update(helper_ids or [])
    audience.discard(actor_id)
    audience.discard(None)
    audience.discard("")
    return list(audience)


# ── Leave / branch weekly-off awareness ─────────────────────────────────────

def is_working_day_for(db: Session, user: User, on_date: date) -> bool:
    """False if `on_date` is the user's branch's weekly-off day, or a day
    covered by one of the user's APPROVED leave requests."""
    if user.branch_id:
        branch = db.query(Branch).filter(Branch.id == user.branch_id).first()
        if branch and branch.weekly_off_days:
            import json
            try:
                off_days = json.loads(branch.weekly_off_days)
            except Exception:
                off_days = []
            if on_date.weekday() in off_days:
                return False
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.user_id == user.id, LeaveRequest.status == "APPROVED",
        LeaveRequest.start_date <= on_date, LeaveRequest.end_date >= on_date,
    ).first()
    return leave is None


def next_working_day_for(db: Session, user: User, from_date: date, max_days: int = 14) -> date:
    """First date >= from_date that is_working_day_for() accepts."""
    d = from_date
    for _ in range(max_days):
        if is_working_day_for(db, user, d):
            return d
        d = d + timedelta(days=1)
    return from_date  # give up rather than loop forever — fire on the original date
