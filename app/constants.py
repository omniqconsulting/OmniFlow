"""
Feature Flag System — Phase 0-I
Centralised feature catalog, plan limits, and gate helpers.
"""
import os

# ── Plans ──────────────────────────────────────────────────────────────────────
PLAN_TRIAL        = "TRIAL"
PLAN_STARTER      = "STARTER"
PLAN_PROFESSIONAL = "PROFESSIONAL"
PLAN_ENTERPRISE   = "ENTERPRISE"

PLAN_ORDER = {
    PLAN_TRIAL:        -1,
    PLAN_STARTER:       0,
    PLAN_PROFESSIONAL:  1,
    PLAN_ENTERPRISE:    2,
}

PLAN_LABELS = {
    PLAN_TRIAL:        "Trial",
    PLAN_STARTER:      "Starter",
    PLAN_PROFESSIONAL: "Professional",
    PLAN_ENTERPRISE:   "Enterprise",
}

# ── Full feature catalog ───────────────────────────────────────────────────────
# Each entry: label, category, min_plan
# category groups are used to render the plan comparison table

FEATURE_CATALOG = {
    # ── Core ──────────────────────────────────────────────────────────────────
    "TICKETS":              ("Ticket Management",            "Core",         PLAN_STARTER),
    "KANBAN":               ("Kanban Board View",            "Core",         PLAN_STARTER),
    "TICKET_HELPERS":       ("Ticket Collaborators",         "Core",         PLAN_STARTER),
    "CHECKLISTS":           ("Checklists",                   "Core",         PLAN_STARTER),
    "CHECKLIST_COMMENTS":   ("Checklist Comments",           "Core",         PLAN_STARTER),
    "MEDIA_UPLOAD":         ("Photo / File Uploads",         "Core",         PLAN_STARTER),
    "NOTIFICATIONS":        ("In-App Notifications",         "Core",         PLAN_STARTER),
    "EMPLOYEES":            ("Employee Management",          "Core",         PLAN_STARTER),
    # ── Analytics ─────────────────────────────────────────────────────────────
    "KPI_SELF":             ("Personal KPI Dashboard",       "Analytics",    PLAN_STARTER),
    "KPI_CHARTS_ADMIN":     ("Team KPI Dashboard",           "Analytics",    PLAN_PROFESSIONAL),
    "ADVANCED_ANALYTICS":   ("Advanced Analytics",           "Analytics",    PLAN_PROFESSIONAL),
    "CSV_EXPORT":           ("Data Export (CSV)",            "Analytics",    PLAN_PROFESSIONAL),
    # ── Efficiency ────────────────────────────────────────────────────────────
    "BULK_IMPORT":          ("Bulk CSV Import",              "Efficiency",   PLAN_PROFESSIONAL),
    "RECURRING_CHECKLISTS": ("Recurring Checklists",         "Efficiency",   PLAN_PROFESSIONAL),
    "TICKET_ESCALATION":    ("Auto Ticket Escalation",       "Efficiency",   PLAN_PROFESSIONAL),
    # ── Scale ─────────────────────────────────────────────────────────────────
    "MULTI_BRANCH":         ("Multi-Branch Support",         "Scale",        PLAN_PROFESSIONAL),
    "MANAGER_ROLES":        ("Manager Role",                 "Scale",        PLAN_PROFESSIONAL),
    # ── Integration ───────────────────────────────────────────────────────────
    "API_ACCESS":           ("API Access",                   "Integration",  PLAN_ENTERPRISE),
    "WHITE_LABEL":          ("White Labelling",              "Integration",  PLAN_ENTERPRISE),
    "CUSTOM_FIELDS":        ("Custom Ticket Fields",         "Integration",  PLAN_ENTERPRISE),
    # ── Compliance ────────────────────────────────────────────────────────────
    "AUDIT_LOG":            ("Audit Log",                    "Compliance",   PLAN_ENTERPRISE),
    "SSO":                  ("Single Sign-On (SSO)",         "Compliance",   PLAN_ENTERPRISE),
    "SLA_MANAGEMENT":       ("SLA Management",               "Compliance",   PLAN_ENTERPRISE),
    # ── Support ───────────────────────────────────────────────────────────────
    "DEDICATED_SUPPORT":    ("Dedicated Support",            "Support",      PLAN_ENTERPRISE),
    # ── Modules (domain-agnostic, SA opts-in per tenant) ──────────────────────
    "FMS":                  ("Flow Board / Pipeline",        "Modules",      PLAN_PROFESSIONAL),
}

# Back-compat: keep the flat FEATURES dict so existing has_feature() calls work
FEATURES = {k: v[2] for k, v in FEATURE_CATALOG.items()}

# ── Quantitative limits per plan ───────────────────────────────────────────────
# None = unlimited
PLAN_LIMITS = {
    PLAN_TRIAL:        {"max_users": 3,    "max_branches": 1, "max_checklist_templates": 5,  "max_tickets_open": 10,   "max_fms_flows": 0, "ai_daily_limit": 5},
    PLAN_STARTER:      {"max_users": 15,   "max_branches": 2, "max_checklist_templates": 20, "max_tickets_open": None, "max_fms_flows": 1, "ai_daily_limit": 20},
    PLAN_PROFESSIONAL: {"max_users": 50,   "max_branches": 5, "max_checklist_templates": None,"max_tickets_open": None, "max_fms_flows": 5, "ai_daily_limit": 100},
    PLAN_ENTERPRISE:   {"max_users": None, "max_branches": None,"max_checklist_templates": None,"max_tickets_open": None, "max_fms_flows": None, "ai_daily_limit": None},
}

LIMIT_LABELS = {
    "max_users":                "Team Members",
    "max_branches":             "Branches",
    "max_fms_flows":            "FMS Flows",
    "max_checklist_templates":  "Checklist Templates",
    "max_tickets_open":         "Open Tickets",
}

# ── Feature gate helpers ───────────────────────────────────────────────────────

def has_feature(tenant, feature_name: str, db=None) -> bool:
    """
    Return True if tenant can use feature_name.
    Checks per-tenant overrides first (requires db), then falls back to plan.
    """
    # Per-tenant override (SA can unlock/lock any feature regardless of plan)
    if db is not None:
        try:
            from .database import TenantFeatureOverride
            override = db.query(TenantFeatureOverride).filter(
                TenantFeatureOverride.tenant_id == tenant.id,
                TenantFeatureOverride.feature    == feature_name,
            ).first()
            if override is not None:
                return override.enabled
        except Exception:
            pass  # Table may not exist yet during migration

    if feature_name not in FEATURES:
        return True   # Unknown features are on by default

    required = FEATURES[feature_name]
    current  = getattr(tenant, "plan", PLAN_STARTER) or PLAN_STARTER
    return PLAN_ORDER.get(current, 0) >= PLAN_ORDER.get(required, 0)


def get_limit(tenant, limit_name: str) -> "int | None":
    """Return the quantitative limit for the tenant's plan. None = unlimited."""
    plan   = getattr(tenant, "plan", PLAN_STARTER) or PLAN_STARTER
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS[PLAN_STARTER])
    return limits.get(limit_name)


def within_limit(tenant, limit_name: str, current_count: int) -> bool:
    """Return True if current_count is within the plan's limit."""
    limit = get_limit(tenant, limit_name)
    return limit is None or current_count < limit


def get_plan_features(plan: str) -> dict:
    """Return {feature_name: True/False} for every feature for a given plan."""
    return {
        k: PLAN_ORDER.get(plan, 0) >= PLAN_ORDER.get(v[2], 0)
        for k, v in FEATURE_CATALOG.items()
    }


def feature_label(feature_name: str) -> str:
    return FEATURE_CATALOG.get(feature_name, (feature_name,))[0]


def next_plan(current_plan: str) -> "str | None":
    """Return the next plan up, or None if already on Enterprise."""
    order = [PLAN_STARTER, PLAN_PROFESSIONAL, PLAN_ENTERPRISE]
    try:
        idx = order.index(current_plan)
        return order[idx + 1] if idx + 1 < len(order) else None
    except ValueError:
        return PLAN_STARTER


# ── WhatsApp / MSG91 Templates ────────────────────────────────────────────────
# Foundation registry. Each pipeline brief appends ONE entry here as that
# template is wired to a real trigger. variable_order documents param order
# matching the approved Meta template — the actual send call takes a plain list.
WHATSAPP_TEMPLATES = {
    "omniflow_ticket_assigned": {
        "msg91_template_id": 417221,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["name", "ticket_title", "priority", "due_date"],
    },
    "omniflow_checklist_due": {
        "msg91_template_id": 417222,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["name", "checklist_titles_csv"],
    },
    "omniflow_checklist_overdue": {
        "msg91_template_id": 417223,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["name", "checklist_titles_csv"],
    },
    "omniflow_ticket_unacknowledged": {
        "msg91_template_id": 417225,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["recipient_name", "ticket_title", "assignee_name", "hours"],
    },
    "omniflow_ticket_escalated": {
        "msg91_template_id": 417224,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["recipient_name", "ticket_title", "actor_name"],
    },
    # FMS stage transition — new assignee alert
    # Hi {{1}}, a work order '{{2}}' has moved to stage '{{3}}' and is now
    # assigned to you. Login to OmniFlow to acknowledge.
    "omniflow_fms_stage_transition": {
        "msg91_template_id": 417226,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["name", "ticket_title", "stage_name"],
    },
    # Brief 5 — Registration pipelines (5A, 5B, 5C)
    "omniflow_registration_received": {
        "msg91_template_id": 417218,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["contact_name", "company_name"],
    },
    "omniflow_registration_alert_sa": {
        "msg91_template_id": 418092,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["company_name", "contact_name", "contact_phone"],
    },
    "omniflow_registration_rejected": {
        "msg91_template_id": 417220,
        "namespace": "42a08df0_cdc3_4411_b61b_c1985222c017",
        "variable_order": ["reason"],
    },
}

MSG91_AUTH_KEY = os.environ.get("MSG91_AUTH_KEY", "")
MSG91_WA_NUMBER = os.environ.get("MSG91_WA_NUMBER", "")
# Brief 5 — SA alert phone (international format, no +, no spaces)
SA_ALERT_PHONE = os.environ.get("SA_ALERT_PHONE", "")
