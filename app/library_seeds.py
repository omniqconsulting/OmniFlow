"""Phase 0-K-12: Seed the library with 3 industry onboarding bundles.

Run once at startup — idempotent (checks if already seeded).
Bundles: Factory (default), Construction, Pharma.
"""
from __future__ import annotations
import json, logging
from .database import (
    LibraryLabelBundle, LibraryChecklistTemplate,
    LibraryFlowTemplate, LibraryFlowStage,
    LibrarySubmoduleDefinition, LibraryOnboardingBundle,
)

log = logging.getLogger(__name__)

# ── Field-type helpers ────────────────────────────────────────────────────────

def _field(fid: str, label: str, ftype: str, required: bool = False,
           options: list | None = None) -> dict:
    d: dict = {"id": fid, "label": label, "type": ftype,
               "required": required, "order": 0}
    if options:
        d["options"] = options
    return d


# ── Seed data definitions ─────────────────────────────────────────────────────

LABEL_BUNDLES = [
    # name, industry, overrides dict (None = use system default)
    {
        "name": "Factory / Manufacturing (Default)",
        "industry": "Manufacturing",
        "description": "Generic factory vocabulary — tickets, checklists, branches, departments, employees.",
    },
    {
        "name": "Restaurant / F&B",
        "industry": "Restaurant / F&B",
        "description": "F&B vocabulary — tasks, outlets, sections, staff.",
        "ticket_s": "Task",    "ticket_p": "Tasks",
        "branch_s": "Outlet",  "branch_p": "Outlets",
        "department_s": "Section", "department_p": "Sections",
        "employee_s": "Staff", "employee_p": "Staff",
    },
    {
        "name": "Construction",
        "industry": "Construction",
        "description": "Construction vocabulary — work orders, sites, crews, workers.",
        "ticket_s": "Work Order", "ticket_p": "Work Orders",
        "branch_s": "Site",       "branch_p": "Sites",
        "department_s": "Crew",   "department_p": "Crews",
        "employee_s": "Worker",   "employee_p": "Workers",
    },
    {
        "name": "Retail",
        "industry": "Retail",
        "description": "Retail vocabulary — issues, stores, sections.",
        "ticket_s": "Issue",      "ticket_p": "Issues",
        "branch_s": "Store",      "branch_p": "Stores",
        "department_s": "Section","department_p": "Sections",
    },
    {
        "name": "Healthcare",
        "industry": "Healthcare",
        "description": "Healthcare vocabulary — requests, clinics, wards, staff.",
        "ticket_s": "Request",    "ticket_p": "Requests",
        "branch_s": "Clinic",     "branch_p": "Clinics",
        "department_s": "Ward",   "department_p": "Wards",
        "employee_s": "Staff",    "employee_p": "Staff",
    },
    {
        "name": "Pharma / Laboratory",
        "industry": "Pharma",
        "description": "Pharmaceutical / lab vocabulary — work orders, facilities, labs, scientists.",
        "ticket_s": "Work Order",    "ticket_p": "Work Orders",
        "branch_s": "Facility",      "branch_p": "Facilities",
        "department_s": "Laboratory","department_p": "Laboratories",
        "employee_s": "Scientist",   "employee_p": "Scientists",
    },
    {
        "name": "Logistics",
        "industry": "Logistics",
        "description": "Logistics vocabulary — delivery tasks, hubs, teams, drivers.",
        "ticket_s": "Delivery Task", "ticket_p": "Delivery Tasks",
        "branch_s": "Hub",           "branch_p": "Hubs",
        "department_s": "Team",      "department_p": "Teams",
        "employee_s": "Driver",      "employee_p": "Drivers",
    },
    {
        "name": "Hotel / Hospitality",
        "industry": "Hotel / Hospitality",
        "description": "Hospitality vocabulary — service requests, properties, staff.",
        "ticket_s": "Service Request", "ticket_p": "Service Requests",
        "branch_s": "Property",        "branch_p": "Properties",
        "employee_s": "Staff",         "employee_p": "Staff",
    },
]


CHECKLIST_TEMPLATES = {
    "Manufacturing": [
        {
            "name": "Daily Machine Safety Inspection",
            "description": "Pre-shift safety check for all production machines.",
            "frequency": "DAILY",
            "assigned_to_role": "EMPLOYEE",
            "proof_required": True,
            "notes": "Must be completed before first shift starts.",
        },
        {
            "name": "End-of-Shift Housekeeping",
            "description": "Clean workstation, return tools, dispose of waste.",
            "frequency": "DAILY",
            "assigned_to_role": "EMPLOYEE",
            "proof_required": False,
        },
        {
            "name": "Weekly Equipment Maintenance Log",
            "description": "Lubrication, belt checks, filter replacement.",
            "frequency": "WEEKLY",
            "assigned_to_role": "MANAGER",
            "proof_required": True,
        },
    ],
    "Construction": [
        {
            "name": "Daily Site Safety Walkthrough",
            "description": "Check PPE compliance, hazard identification, signage.",
            "frequency": "DAILY",
            "assigned_to_role": "MANAGER",
            "proof_required": True,
        },
        {
            "name": "Tool & Equipment Inventory Check",
            "description": "Verify all tools are accounted for and undamaged.",
            "frequency": "DAILY",
            "assigned_to_role": "EMPLOYEE",
            "proof_required": False,
        },
        {
            "name": "Weekly Progress Report Checklist",
            "description": "Document completed work, blockers, material needs.",
            "frequency": "WEEKLY",
            "assigned_to_role": "MANAGER",
            "proof_required": False,
        },
    ],
    "Pharma": [
        {
            "name": "Lab Cleanliness & Sterility Check",
            "description": "Verify clean-room conditions, equipment sterility.",
            "frequency": "DAILY",
            "assigned_to_role": "EMPLOYEE",
            "proof_required": True,
        },
        {
            "name": "Cold Storage Temperature Log",
            "description": "Record temperature of all refrigeration units twice daily.",
            "frequency": "DAILY",
            "assigned_to_role": "EMPLOYEE",
            "proof_required": False,
        },
        {
            "name": "Monthly Calibration Checklist",
            "description": "Calibrate scales, pH meters, spectrophotometers.",
            "frequency": "MONTHLY",
            "assigned_to_role": "MANAGER",
            "proof_required": True,
        },
    ],
}


FLOW_TEMPLATES = {
    "Manufacturing": {
        "name": "Standard Production Flow",
        "description": "Default 5-stage production ticket workflow for manufacturing.",
        "stages": [
            {"name": "Open",        "color": "#3b82f6", "is_terminal": False, "order": 0},
            {"name": "In Progress", "color": "#f59e0b", "is_terminal": False, "order": 1},
            {"name": "QC Review",   "color": "#8b5cf6", "is_terminal": False, "order": 2},
            {"name": "Done",        "color": "#10b981", "is_terminal": True,  "order": 3},
            {"name": "Closed",      "color": "#6b7280", "is_terminal": True,  "order": 4},
        ],
    },
    "Construction": {
        "name": "Site Work Order Flow",
        "description": "Work order lifecycle for construction site tasks.",
        "stages": [
            {"name": "Raised",      "color": "#ef4444", "is_terminal": False, "order": 0},
            {"name": "Assigned",    "color": "#3b82f6", "is_terminal": False, "order": 1},
            {"name": "In Progress", "color": "#f59e0b", "is_terminal": False, "order": 2},
            {"name": "Inspection",  "color": "#8b5cf6", "is_terminal": False, "order": 3},
            {"name": "Completed",   "color": "#10b981", "is_terminal": True,  "order": 4},
        ],
    },
    "Pharma": {
        "name": "Lab Work Order Flow",
        "description": "Controlled workflow for lab requests with QC and sign-off.",
        "stages": [
            {"name": "Requested",   "color": "#3b82f6", "is_terminal": False, "order": 0},
            {"name": "In Analysis", "color": "#f59e0b", "is_terminal": False, "order": 1},
            {"name": "QC Hold",     "color": "#ef4444", "is_terminal": False, "order": 2},
            {"name": "QC Passed",   "color": "#10b981", "is_terminal": False, "order": 3},
            {"name": "Released",    "color": "#6ee7b7", "is_terminal": True,  "order": 4},
            {"name": "Rejected",    "color": "#f87171", "is_terminal": True,  "order": 5},
        ],
    },
}


SUBMODULE_DEFINITIONS = {
    "Manufacturing": {
        "name": "Machine Breakdown Report",
        "description": "Capture machine failure details for root cause analysis.",
        "fields": [
            _field("f1", "Machine ID / Name",   "text",     required=True),
            _field("f2", "Failure Description", "longtext", required=True),
            _field("f3", "Time of Failure",     "datetime", required=True),
            _field("f4", "Root Cause Category", "dropdown", required=True,
                   options=["Mechanical", "Electrical", "Operator Error", "Material", "Other"]),
            _field("f5", "Photo of Damage",     "photo",    required=False),
            _field("f6", "Estimated Downtime (hrs)", "number", required=True),
            _field("f7", "Safety Incident?",    "yesno",    required=True),
        ],
    },
    "Construction": {
        "name": "Site Incident Report",
        "description": "Record site safety incidents and near-misses.",
        "fields": [
            _field("f1", "Incident Date & Time", "datetime",  required=True),
            _field("f2", "Location on Site",     "text",      required=True),
            _field("f3", "Description",          "longtext",  required=True),
            _field("f4", "Incident Type",        "dropdown",  required=True,
                   options=["Injury", "Near Miss", "Property Damage", "Environmental"]),
            _field("f5", "Were Authorities Notified?", "yesno", required=True),
            _field("f6", "Photo Evidence",       "photo",    required=False),
            _field("f7", "Corrective Action",    "longtext", required=False),
        ],
    },
    "Pharma": {
        "name": "Deviation Report",
        "description": "Document process deviations for regulatory compliance.",
        "fields": [
            _field("f1", "Batch Number",         "text",     required=True),
            _field("f2", "Process Step",         "text",     required=True),
            _field("f3", "Deviation Description","longtext", required=True),
            _field("f4", "Deviation Category",   "dropdown", required=True,
                   options=["Critical", "Major", "Minor"]),
            _field("f5", "Date / Time Observed", "datetime", required=True),
            _field("f6", "Immediate Action Taken","longtext",required=True),
            _field("f7", "CAPA Required?",       "yesno",   required=True),
            _field("f8", "Supporting Document",  "file",    required=False),
        ],
    },
}


# ── Seeder ────────────────────────────────────────────────────────────────────

def seed_library(db) -> None:
    """Idempotent: seed library data if not already present."""
    if db.query(LibraryLabelBundle).count() > 0:
        return   # already seeded

    log.info("Seeding Configuration Library (Phase 0-K) …")
    _label_ids: dict[str, str] = {}

    # ── Label bundles ─────────────────────────────────────────────────────────
    for data in LABEL_BUNDLES:
        lb = LibraryLabelBundle(
            name=data["name"],
            description=data.get("description", ""),
            industry=data.get("industry"),
            is_system=True,
            ticket_s=data.get("ticket_s"),    ticket_p=data.get("ticket_p"),
            checklist_s=data.get("checklist_s"), checklist_p=data.get("checklist_p"),
            branch_s=data.get("branch_s"),    branch_p=data.get("branch_p"),
            department_s=data.get("department_s"), department_p=data.get("department_p"),
            employee_s=data.get("employee_s"), employee_p=data.get("employee_p"),
        )
        db.add(lb)
        db.flush()
        _label_ids[data["industry"]] = lb.id

    # ── Checklist templates ───────────────────────────────────────────────────
    _cl_ids: dict[str, list[str]] = {}
    for industry, checklists in CHECKLIST_TEMPLATES.items():
        _cl_ids[industry] = []
        for cl_data in checklists:
            ct = LibraryChecklistTemplate(
                name=cl_data["name"],
                description=cl_data.get("description", ""),
                frequency=cl_data.get("frequency", "DAILY"),
                industry=industry,
                proof_required=cl_data.get("proof_required", False),
                assigned_to_role=cl_data.get("assigned_to_role", "EMPLOYEE"),
                notes=cl_data.get("notes"),
                is_system=True, status="ACTIVE",
            )
            db.add(ct)
            db.flush()
            _cl_ids[industry].append(ct.id)

    # ── Flow templates ────────────────────────────────────────────────────────
    _flow_ids: dict[str, str] = {}
    for industry, ft_data in FLOW_TEMPLATES.items():
        ft = LibraryFlowTemplate(
            name=ft_data["name"],
            description=ft_data["description"],
            industry=industry,
            is_system=True, status="ACTIVE",
        )
        db.add(ft)
        db.flush()
        for s in ft_data["stages"]:
            db.add(LibraryFlowStage(
                template_id=ft.id,
                name=s["name"], color=s["color"],
                is_terminal=s["is_terminal"], order=s["order"],
            ))
        _flow_ids[industry] = ft.id

    # ── Sub-module definitions ────────────────────────────────────────────────
    _sub_ids: dict[str, str] = {}
    for industry, sm_data in SUBMODULE_DEFINITIONS.items():
        fields = sm_data["fields"]
        for i, f in enumerate(fields):
            f["order"] = i
        sm = LibrarySubmoduleDefinition(
            name=sm_data["name"],
            description=sm_data["description"],
            industry=industry,
            is_system=True, status="ACTIVE",
            fields_json=json.dumps(fields),
        )
        db.add(sm)
        db.flush()
        _sub_ids[industry] = sm.id

    # ── Onboarding bundles ────────────────────────────────────────────────────
    bundles = [
        {
            "name": "Factory Default Onboarding",
            "industry": "Manufacturing",
            "description": "Standard factory setup: safety checklists, production flow, basic labels.",
            "notes": "Suitable for most manufacturing / light industrial tenants.",
        },
        {
            "name": "Construction Site Onboarding",
            "industry": "Construction",
            "description": "Construction vocabulary, site safety checklists, work order flow.",
        },
        {
            "name": "Pharma / Lab Onboarding",
            "industry": "Pharma",
            "description": "Controlled-environment checklists, deviation sub-module, lab work order flow.",
        },
    ]
    for b in bundles:
        ind = b["industry"]
        ob = LibraryOnboardingBundle(
            name=b["name"],
            description=b["description"],
            industry=ind,
            is_system=True, version=1,
            notes=b.get("notes"),
            label_bundle_id=_label_ids.get(ind),
            checklist_ids_json=json.dumps(_cl_ids.get(ind, [])),
            flow_template_ids_json=json.dumps([_flow_ids[ind]] if ind in _flow_ids else []),
            submodule_ids_json=json.dumps([_sub_ids[ind]] if ind in _sub_ids else []),
        )
        db.add(ob)

    db.commit()
    log.info("Library seeded: %d label bundles, %d checklists, %d flows, %d sub-modules, 3 bundles.",
             len(LABEL_BUNDLES),
             sum(len(v) for v in CHECKLIST_TEMPLATES.values()),
             len(FLOW_TEMPLATES),
             len(SUBMODULE_DEFINITIONS))
