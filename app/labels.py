"""Phase 0-J: Domain Agnosticism — Label Configuration System.

Every tenant can rename core concepts so the UI feels native to their industry.
Templates access labels via the `L` dict injected into every response:
    {{ L.Ticket }}        → "Work Order"  (or whatever the tenant configured)

Core keys:  Ticket/Tickets, Checklist/Checklists, Branch/Branches,
            Department/Departments, Employee/Employees
"""

from __future__ import annotations
from typing import Optional

# ── Default labels (generic) ─────────────────────────────────────────────────

DEFAULTS: dict[str, tuple[str, str]] = {
    # concept           : (singular,          plural)
    "ticket":            ("Ticket",           "Tickets"),
    "checklist":         ("Checklist",        "Checklists"),
    "branch":            ("Branch",           "Branches"),
    "department":        ("Department",       "Departments"),
    "employee":          ("Employee",         "Employees"),
}

# ── Industry presets ──────────────────────────────────────────────────────────
# A preset only needs to supply the concepts it overrides; the rest fall back
# to DEFAULTS.  The dict maps concept -> (singular, plural).

INDUSTRY_PRESETS: dict[str, dict[str, tuple[str, str]]] = {
    "Manufacturing": {},  # all defaults — explicit entry so it appears in UI
    "Restaurant / F&B": {
        "ticket":     ("Task",     "Tasks"),
        "branch":     ("Outlet",   "Outlets"),
        "department": ("Section",  "Sections"),
        "employee":   ("Staff",    "Staff"),
    },
    "Retail": {
        "ticket":     ("Issue",    "Issues"),
        "branch":     ("Store",    "Stores"),
        "department": ("Section",  "Sections"),
    },
    "Healthcare": {
        "ticket":     ("Request",  "Requests"),
        "branch":     ("Clinic",   "Clinics"),
        "department": ("Ward",     "Wards"),
        "employee":   ("Staff",    "Staff"),
    },
    "Construction": {
        "ticket":     ("Work Order",  "Work Orders"),
        "branch":     ("Site",        "Sites"),
        "department": ("Crew",        "Crews"),
        "employee":   ("Worker",      "Workers"),
    },
    "Logistics": {
        "ticket":     ("Delivery Task",  "Delivery Tasks"),
        "branch":     ("Hub",            "Hubs"),
        "department": ("Team",           "Teams"),
        "employee":   ("Driver",         "Drivers"),
    },
    "Hotel / Hospitality": {
        "ticket":     ("Service Request", "Service Requests"),
        "branch":     ("Property",        "Properties"),
        "employee":   ("Staff",           "Staff"),
    },
    "Education": {
        "ticket":     ("Request",  "Requests"),
        "branch":     ("Campus",   "Campuses"),
        "department": ("Faculty",  "Faculties"),
        "employee":   ("Staff",    "Staff"),
    },
}

# Convenience list for dropdowns
INDUSTRY_NAMES: list[str] = list(INDUSTRY_PRESETS.keys())


# ── Build the L dict from raw values ─────────────────────────────────────────

def _build_L(**overrides) -> dict[str, str]:
    """Build the full L dict.  Pass concept_s / concept_p kwargs to override defaults."""
    def _v(concept: str, suffix: str) -> str:
        key = f"{concept}_{suffix}"
        val = overrides.get(key)
        return val if val else DEFAULTS[concept][0 if suffix == "s" else 1]

    L: dict[str, str] = {}
    for concept in DEFAULTS:
        s = _v(concept, "s")
        p = _v(concept, "p")
        # Title-case keys for headings/nav
        tc = concept.replace("_", " ").title().replace(" ", "")
        L[tc]          = s
        L[tc + "s"]    = p
        # lower-case keys for inline prose
        L[concept]     = s.lower()
        L[concept + "s"] = p.lower()

    # Friendly aliases — fix irregular plurals ("Branchs" → "Branches")
    L["Ticket"]      = L.get("Ticket",      DEFAULTS["ticket"][0])
    L["Tickets"]     = L.get("Tickets",     DEFAULTS["ticket"][1])
    L["Branch"]      = L.get("Branch",      DEFAULTS["branch"][0])
    L["Branches"]    = L.get("Branchs",     DEFAULTS["branch"][1])
    L["Department"]  = L.get("Department",  DEFAULTS["department"][0])
    L["Departments"] = L.get("Departments", DEFAULTS["department"][1])
    L["Employee"]    = L.get("Employee",    DEFAULTS["employee"][0])
    L["Employees"]   = L.get("Employees",   DEFAULTS["employee"][1])
    return L


# ── Default L dict (no tenant context) ───────────────────────────────────────

DEFAULT_L: dict[str, str] = _build_L()


# ── Public helper ─────────────────────────────────────────────────────────────

def get_labels(db, tenant_id: Optional[str]) -> dict[str, str]:
    """Return the full L dict for a tenant, falling back to defaults."""
    if db is None or tenant_id is None:
        return DEFAULT_L

    from app.database import TenantLabelConfig
    row: Optional[TenantLabelConfig] = (
        db.query(TenantLabelConfig)
        .filter(TenantLabelConfig.tenant_id == tenant_id)
        .first()
    )
    if row is None:
        return DEFAULT_L

    return _build_L(
        ticket_s=row.ticket_s,       ticket_p=row.ticket_p,
        checklist_s=row.checklist_s, checklist_p=row.checklist_p,
        branch_s=row.branch_s,       branch_p=row.branch_p,
        department_s=row.department_s, department_p=row.department_p,
        employee_s=row.employee_s,   employee_p=row.employee_p,
    )


def get_preset_labels(industry: str) -> dict[str, str]:
    """Return the L dict for a named industry preset."""
    overrides = INDUSTRY_PRESETS.get(industry, {})
    kwargs = {}
    for concept, (s, p) in overrides.items():
        kwargs[f"{concept}_s"] = s
        kwargs[f"{concept}_p"] = p
    return _build_L(**kwargs)
