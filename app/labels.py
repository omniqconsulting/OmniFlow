"""Phase 0-J / Phase 4: Domain Agnosticism — Label Configuration System.

Every tenant can rename core concepts so the UI feels native to their industry.
Templates access labels via the `L` dict injected into every response:
    {{ L.Ticket }}        → "Work Order"  (or whatever the tenant configured)
    {{ L.Material }}      → "Product" / "SKU" / "Item"
    {{ L.stock_in }}      → "GRN" / "Receiving" / "Purchase Receipt"

Core keys:  Ticket/Tickets, Checklist/Checklists, Branch/Branches,
            Department/Departments, Employee/Employees
Inventory:  Inventory/Inventories, Material/Materials, stock_in, stock_out,
            Adjustment, PurchaseOrder/PurchaseOrders, Supplier/Suppliers,
            StoreManager/StoreManagers
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
    # ── Inventory (Phase 4) ───────────────────────────────────────────────────
    "inventory":         ("Inventory",        "Inventories"),
    "material":          ("Material",         "Materials"),
    "stock_in":          ("Stock In",         "Stock In"),      # rarely pluralised
    "stock_out":         ("Stock Out",        "Stock Out"),
    "adjustment":        ("Adjustment",       "Adjustments"),
    "purchase_order":    ("Purchase Order",   "Purchase Orders"),
    "supplier":          ("Supplier",         "Suppliers"),
    "store_manager":     ("Store Manager",    "Store Managers"),
}

# ── Industry inventory presets ────────────────────────────────────────────────
INVENTORY_PRESETS: dict[str, dict[str, tuple[str, str]]] = {
    "Manufacturing": {
        "material":       ("Raw Material",       "Raw Materials"),
        "stock_in":       ("GRN",                "GRNs"),
        "stock_out":      ("Issue",              "Issues"),
        "store_manager":  ("Store Manager",      "Store Managers"),
    },
    "Retail": {
        "inventory":      ("Store",              "Stores"),
        "material":       ("Product",            "Products"),
        "stock_in":       ("Receiving",          "Receivings"),
        "stock_out":      ("Sale Deduction",     "Sale Deductions"),
        "store_manager":  ("Stock Controller",   "Stock Controllers"),
    },
    "Restaurant / F&B": {
        "material":       ("Ingredient",         "Ingredients"),
        "stock_in":       ("Receiving",          "Receivings"),
        "stock_out":      ("Consumption",        "Consumptions"),
        "purchase_order": ("Procurement Order",  "Procurement Orders"),
        "store_manager":  ("Kitchen Store Lead", "Kitchen Store Leads"),
    },
    "Construction": {
        "material":       ("Material",           "Materials"),
        "stock_in":       ("Site Delivery",      "Site Deliveries"),
        "stock_out":      ("Site Issue",         "Site Issues"),
        "supplier":       ("Vendor",             "Vendors"),
        "store_manager":  ("Site Store Incharge","Site Store Incharges"),
    },
    "Logistics": {
        "material":       ("Item",               "Items"),
        "stock_in":       ("Inbound",            "Inbounds"),
        "stock_out":      ("Outbound",           "Outbounds"),
        "store_manager":  ("Warehouse Manager",  "Warehouse Managers"),
    },
    "Healthcare": {
        "material":       ("Supply",             "Supplies"),
        "stock_in":       ("Procurement",        "Procurements"),
        "stock_out":      ("Dispensing",         "Dispensings"),
        "store_manager":  ("Supply Chain Lead",  "Supply Chain Leads"),
    },
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
        tc = concept.replace("_", " ").title().replace(" ", "")   # "purchase_order" → "PurchaseOrder"
        L[tc]          = s
        L[tc + "s"]    = p   # PurchaseOrders etc.  (may dupe for already-plural like "Employees")
        # lower-case keys for inline prose
        L[concept]     = s.lower()
        L[concept + "s"] = p.lower()

    # Friendly aliases used in templates
    L["Ticket"]        = L["Ticket"]          if "Ticket"        in L else DEFAULTS["ticket"][0]
    L["Tickets"]       = L["Tickets"]         if "Tickets"       in L else DEFAULTS["ticket"][1]
    # stock_in / stock_out don't pluralise meaningfully; keep as-is
    L["stock_in"]      = _v("stock_in",   "s")
    L["stock_out"]     = _v("stock_out",  "s")
    L["StockIn"]       = L["stock_in"]
    L["StockOut"]      = L["stock_out"]
    L["Adjustment"]    = _v("adjustment", "s")
    L["Adjustments"]   = _v("adjustment", "p")
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
        # Core
        ticket_s=row.ticket_s,       ticket_p=row.ticket_p,
        checklist_s=row.checklist_s, checklist_p=row.checklist_p,
        branch_s=row.branch_s,       branch_p=row.branch_p,
        department_s=row.department_s, department_p=row.department_p,
        employee_s=row.employee_s,   employee_p=row.employee_p,
        # Inventory
        inventory_s=row.inventory_s,           inventory_p=row.inventory_p,
        material_s=row.material_s,             material_p=row.material_p,
        stock_in_s=row.stock_in_s,
        stock_out_s=row.stock_out_s,
        adjustment_s=row.adjustment_s,
        purchase_order_s=row.purchase_order_s, purchase_order_p=row.purchase_order_p,
        supplier_s=row.supplier_s,             supplier_p=row.supplier_p,
        store_manager_s=row.store_manager_s,   store_manager_p=row.store_manager_p,
    )


def get_preset_labels(industry: str) -> dict[str, str]:
    """Return the L dict for a named industry preset (core + inventory)."""
    core_over = INDUSTRY_PRESETS.get(industry, {})
    inv_over  = INVENTORY_PRESETS.get(industry, {})
    merged    = {**core_over, **inv_over}

    kwargs = {}
    for concept, (s, p) in merged.items():
        kwargs[f"{concept}_s"] = s
        kwargs[f"{concept}_p"] = p
    return _build_L(**kwargs)
