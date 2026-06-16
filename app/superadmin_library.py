"""Phase 0-K: Configuration Library — Super Admin Routes.

Router prefix: /superadmin/library
Tabs: flows | submodules | checklists | labels | onboarding
"""
from __future__ import annotations
import json, logging
from datetime import datetime, date as _date
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from markupsafe import Markup as _Markup
import os

from .database import (
    get_db, new_id,
    SuperAdmin, Tenant, User,
    TenantLabelConfig, TenantDeployedItem,
    ChecklistTemplate,
    LibraryFlowTemplate, LibraryFlowStage,
    LibrarySubmoduleDefinition, LibraryChecklistTemplate,
    LibraryLabelBundle, LibraryOnboardingBundle,
)
from .superadmin_auth import get_current_sa
from .constants import get_limit, PLAN_LABELS

router = APIRouter(prefix="/superadmin/library")
BASE_DIR  = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["from_json"] = lambda s: (json.loads(s) if s else [])


class _OrmEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        if isinstance(obj, (datetime, _date)):
            return obj.isoformat()
        return super().default(obj)


templates.env.filters["tojson"] = lambda v: _Markup(json.dumps(v, cls=_OrmEncoder))
log = logging.getLogger(__name__)

FIELD_TYPES = [
    ("text",      "Short Text"),
    ("longtext",  "Long Text"),
    ("number",    "Number"),
    ("date",      "Date"),
    ("datetime",  "Date + Time"),
    ("yesno",     "Yes / No"),
    ("dropdown",  "Dropdown"),
    ("photo",     "Photo Upload"),
    ("file",      "File Upload"),
    ("signature", "Signature"),
]

STATUS_OPTIONS = ["DRAFT", "ACTIVE", "DEPRECATED"]

def _r(path: str):
    return RedirectResponse(path, status_code=302)

def _lib_ctx(sa, tab: str, **kwargs) -> dict:
    return {"sa": sa, "active_tab": tab, **kwargs}


def _flow_cap_info(db, tenants: list) -> dict:
    """Return {tenant_id: {current, limit, plan_label, at_cap}} for FMS flows."""
    from .database import FMSFlow
    result = {}
    for t in tenants:
        current = db.query(FMSFlow).filter(
            FMSFlow.tenant_id == t.id,
            FMSFlow.is_deleted == False,
        ).count()
        limit = get_limit(t, "max_fms_flows")
        result[t.id] = {
            "current": current,
            "limit": limit,
            "plan_label": PLAN_LABELS.get(t.plan, t.plan),
            "at_cap": limit is not None and current >= limit,
        }
    return result


def _checklist_cap_info(db, tenants: list) -> dict:
    """Return {tenant_id: {current, limit, plan_label, at_cap}} for checklist templates."""
    from .database import ChecklistTemplate
    result = {}
    for t in tenants:
        current = db.query(ChecklistTemplate).filter(
            ChecklistTemplate.tenant_id == t.id,
            ChecklistTemplate.is_deleted == False,
        ).count()
        limit = get_limit(t, "max_checklist_templates")
        result[t.id] = {
            "current": current,
            "limit": limit,
            "plan_label": PLAN_LABELS.get(t.plan, t.plan),
            "at_cap": limit is not None and current >= limit,
        }
    return result


# ── Library home ───────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def library_home(sa: SuperAdmin = Depends(get_current_sa)):
    return _r("/superadmin/library/flows")


# ═══════════════════════════════════════════════════════════════════════════════
# FLOWS (0-K-2)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/flows", response_class=HTMLResponse)
def lib_flows(request: Request, sa: SuperAdmin = Depends(get_current_sa),
              db: Session = Depends(get_db)):
    flows = db.query(LibraryFlowTemplate).order_by(
        LibraryFlowTemplate.status, LibraryFlowTemplate.name).all()
    return templates.TemplateResponse(request, "superadmin/library_flows.html",
        _lib_ctx(sa, "flows", flows=flows))


@router.get("/flows/new", response_class=HTMLResponse)
def lib_flow_new_page(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                      db: Session = Depends(get_db)):
    submodules = db.query(LibrarySubmoduleDefinition).filter(
        LibrarySubmoduleDefinition.status != "DEPRECATED"
    ).order_by(LibrarySubmoduleDefinition.is_system.desc(),
               LibrarySubmoduleDefinition.sub_module_type,
               LibrarySubmoduleDefinition.name).all()
    return templates.TemplateResponse(request, "superadmin/library_flow_edit.html",
        _lib_ctx(sa, "flows", flow=None, stages=[], error=None,
                 stages_json="[]",
                 submodules=submodules,
                 submodules_json=json.dumps([{"id": s.id, "name": s.name, "type": s.sub_module_type} for s in submodules]),
                 status_options=STATUS_OPTIONS))


@router.post("/flows/new")
def lib_flow_create(
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""), status: str = Form("DRAFT"),
    notes: str = Form(""), stages_json: str = Form("[]"),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    flow = LibraryFlowTemplate(
        name=name, description=description or None,
        industry=industry or None, status=status, notes=notes or None,
        created_by=sa.id,
    )
    db.add(flow)
    db.flush()
    _save_stages(db, flow.id, stages_json)
    db.commit()
    return _r(f"/superadmin/library/flows/{flow.id}?msg=created")


@router.get("/flows/{flow_id}", response_class=HTMLResponse)
def lib_flow_edit_page(flow_id: str, request: Request,
                        sa: SuperAdmin = Depends(get_current_sa),
                        db: Session = Depends(get_db)):
    flow = _get_flow(db, flow_id)
    deployments = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == flow_id,
        TenantDeployedItem.item_type == "flow",
    ).all()
    tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
    deployed_tenant_ids = {d.tenant_id for d in deployments}
    cap_info = _flow_cap_info(db, tenants)
    submodules = db.query(LibrarySubmoduleDefinition).filter(
        LibrarySubmoduleDefinition.status != "DEPRECATED"
    ).order_by(LibrarySubmoduleDefinition.is_system.desc(),
               LibrarySubmoduleDefinition.sub_module_type,
               LibrarySubmoduleDefinition.name).all()
    return templates.TemplateResponse(request, "superadmin/library_flow_edit.html",
        _lib_ctx(sa, "flows", flow=flow, stages=flow.stages,
                 stages_json=json.dumps([_stage_to_dict(s) for s in flow.stages]),
                 submodules=submodules,
                 submodules_json=json.dumps([{"id": s.id, "name": s.name, "type": s.sub_module_type} for s in submodules]),
                 error=None, status_options=STATUS_OPTIONS,
                 deployments=deployments, tenants=tenants,
                 deployed_tenant_ids=deployed_tenant_ids,
                 cap_info=cap_info,
                 msg=request.query_params.get("msg", "")))


@router.post("/flows/{flow_id}")
def lib_flow_save(
    flow_id: str,
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""), status: str = Form("DRAFT"),
    notes: str = Form(""), stages_json: str = Form("[]"),
    bump_version: bool = Form(False),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    flow = _get_flow(db, flow_id)
    flow.name = name
    flow.description = description or None
    flow.industry = industry or None
    flow.status = status
    flow.notes = notes or None
    flow.updated_at = datetime.utcnow()
    if bump_version:
        flow.version += 1
    # Rebuild stages
    for s in flow.stages:
        db.delete(s)
    db.flush()
    _save_stages(db, flow_id, stages_json)
    db.commit()
    return _r(f"/superadmin/library/flows/{flow_id}?msg=saved")


@router.post("/flows/{flow_id}/duplicate")
def lib_flow_duplicate(flow_id: str, sa: SuperAdmin = Depends(get_current_sa),
                        db: Session = Depends(get_db)):
    src = _get_flow(db, flow_id)
    copy = LibraryFlowTemplate(
        name=f"{src.name} (Copy)", description=src.description,
        industry=src.industry, status="DRAFT", is_system=False,
        notes=src.notes, created_by=sa.id,
    )
    db.add(copy); db.flush()
    for s in src.stages:
        db.add(LibraryFlowStage(
            template_id=copy.id, name=s.name, description=s.description,
            color=s.color, order=s.order, is_terminal=s.is_terminal,
            completion_note_required=bool(s.completion_note_required),
            evidence_required=bool(s.evidence_required),
        ))
    db.commit()
    return _r(f"/superadmin/library/flows/{copy.id}?msg=duplicated")


@router.post("/flows/{flow_id}/deploy")
def lib_flow_deploy(flow_id: str, tenant_id: str = Form(...),
                     notes: str = Form(""),
                     sa: SuperAdmin = Depends(get_current_sa),
                     db: Session = Depends(get_db)):
    from .database import FMSFlow, FMSStage
    flow = _get_flow(db, flow_id)
    tenant = db.query(Tenant).get(tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    # Check for an existing FMSFlow that was deployed from this library template
    existing_fms = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tenant_id,
        FMSFlow.library_flow_id == flow_id,
        FMSFlow.is_deleted == False,
    ).first()

    if existing_fms is None:
        # First-time deploy — check plan cap
        cap = _flow_cap_info(db, [tenant])[tenant_id]
        if cap["at_cap"]:
            limit = cap["limit"]
            current = cap["current"]
            return _r(
                f"/superadmin/library/flows/{flow_id}"
                f"?err=Cannot+deploy+to+{tenant.name}+%E2%80%94+"
                f"they+are+on+the+{cap['plan_label']}+plan+which+allows+"
                f"{limit}+FMS+flow{'s' if limit != 1 else ''}+and+already+have+{current}."
            )
        fms_flow = FMSFlow(
            tenant_id=tenant_id,
            name=flow.name,
            description=flow.description,
            library_flow_id=flow_id,
            library_version_at_deploy=flow.version,
        )
        db.add(fms_flow)
        db.flush()
    else:
        # Re-deploy (update) — rebuild stages in place
        fms_flow = existing_fms
        fms_flow.name = flow.name
        fms_flow.description = flow.description
        fms_flow.library_version_at_deploy = flow.version
        fms_flow.updated_at = datetime.utcnow()
        for stage in list(fms_flow.stages):
            db.delete(stage)
        db.flush()

    # Create FMSStage rows from library stages
    for lib_stage in flow.stages:
        db.add(FMSStage(
            flow_id=fms_flow.id,
            tenant_id=tenant_id,
            name=lib_stage.name,
            description=getattr(lib_stage, 'description', None),
            order=lib_stage.order,
            color=lib_stage.color,
            target_tat_hours=lib_stage.target_tat_hours,
            sub_module_tag=lib_stage.sub_module_tag,
            deployed_submodule_id=lib_stage.submodule_id,
            completion_note_required=bool(lib_stage.completion_note_required),
            evidence_required=bool(lib_stage.evidence_required),
            is_terminal=lib_stage.is_terminal,
        ))

    _upsert_deployed(db, tenant_id, "flow", flow_id, flow.name, flow.version,
                     sa.id, notes)
    db.commit()
    return _r(f"/superadmin/library/flows/{flow_id}?msg=deployed")


@router.post("/flows/{flow_id}/bulk-push")
def lib_flow_bulk_push(flow_id: str,
                        sa: SuperAdmin = Depends(get_current_sa),
                        db: Session = Depends(get_db)):
    """Push the current version to ALL tenants that have already deployed this flow."""
    flow = _get_flow(db, flow_id)
    items = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == flow_id,
        TenantDeployedItem.item_type == "flow",
    ).all()
    for item in items:
        item.deployed_version = flow.version
        item.deployed_at = datetime.utcnow()
        item.deployed_by = sa.id
    db.commit()
    return _r(f"/superadmin/library/flows/{flow_id}?msg=bulk_pushed")


@router.get("/flows/{flow_id}/diff/{tenant_id}", response_class=HTMLResponse)
def lib_flow_diff(flow_id: str, tenant_id: str, request: Request,
                   sa: SuperAdmin = Depends(get_current_sa),
                   db: Session = Depends(get_db)):
    flow = _get_flow(db, flow_id)
    deployed = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == flow_id,
        TenantDeployedItem.item_type == "flow",
        TenantDeployedItem.tenant_id == tenant_id,
    ).first()
    tenant = db.query(Tenant).get(tenant_id)
    return templates.TemplateResponse(request, "superadmin/library_diff.html",
        _lib_ctx(sa, "flows",
                 item_type="flow", item_name=flow.name,
                 library_version=flow.version,
                 deployed_version=deployed.deployed_version if deployed else None,
                 tenant=tenant, flow=flow, deployed=deployed,
                 stages=[_stage_to_dict(s) for s in flow.stages]))


# ═══════════════════════════════════════════════════════════════════════════════
# SUB-MODULES (0-K-3 / 0-K-11)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/submodules", response_class=HTMLResponse)
def lib_submodules(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    # System built-ins first (sorted by type), then custom by name
    items = db.query(LibrarySubmoduleDefinition).order_by(
        LibrarySubmoduleDefinition.is_system.desc(),
        LibrarySubmoduleDefinition.sub_module_type,
        LibrarySubmoduleDefinition.name,
    ).all()
    # Count deployments per item
    from sqlalchemy import func
    counts = db.query(
        TenantDeployedItem.library_item_id,
        func.count(TenantDeployedItem.id).label("cnt"),
    ).filter(TenantDeployedItem.item_type == "submodule").group_by(
        TenantDeployedItem.library_item_id
    ).all()
    deploy_counts = {row.library_item_id: row.cnt for row in counts}
    return templates.TemplateResponse(request, "superadmin/library_submodules.html",
        _lib_ctx(sa, "submodules", items=items, deploy_counts=deploy_counts,
                 msg=request.query_params.get("msg", "")))


@router.get("/submodules/new", response_class=HTMLResponse)
def lib_sub_new_page(request: Request, sa: SuperAdmin = Depends(get_current_sa)):
    return templates.TemplateResponse(request, "superadmin/library_submodule_edit.html",
        _lib_ctx(sa, "submodules", item=None, fields=[], error=None,
                 field_types=FIELD_TYPES, status_options=STATUS_OPTIONS))


@router.post("/submodules/new")
def lib_sub_create(
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""), status: str = Form("DRAFT"),
    fields_json: str = Form("[]"),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    fields = _parse_fields(fields_json)
    sm = LibrarySubmoduleDefinition(
        name=name, description=description or None,
        industry=industry or None, status=status,
        is_system=False, fields_json=json.dumps(fields), created_by=sa.id,
    )
    db.add(sm); db.commit()
    return _r(f"/superadmin/library/submodules/{sm.id}?msg=created")


@router.get("/submodules/{item_id}", response_class=HTMLResponse)
def lib_sub_edit_page(item_id: str, request: Request,
                       sa: SuperAdmin = Depends(get_current_sa),
                       db: Session = Depends(get_db)):
    item = _get_sub(db, item_id)
    fields = json.loads(item.fields_json or "[]")
    deployments = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == item_id,
        TenantDeployedItem.item_type == "submodule",
    ).all()
    tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
    deployed_tenant_ids = {d.tenant_id for d in deployments}
    return templates.TemplateResponse(request, "superadmin/library_submodule_edit.html",
        _lib_ctx(sa, "submodules", item=item, fields=fields,
                 fields_json=item.fields_json, field_types=FIELD_TYPES,
                 error=None, status_options=STATUS_OPTIONS,
                 deployments=deployments, tenants=tenants,
                 deployed_tenant_ids=deployed_tenant_ids,
                 msg=request.query_params.get("msg", "")))


@router.post("/submodules/{item_id}")
def lib_sub_save(
    item_id: str,
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""), status: str = Form("DRAFT"),
    fields_json: str = Form("[]"), bump_version: bool = Form(False),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    item = _get_sub(db, item_id)
    if item.is_system:
        raise HTTPException(403, "System sub-modules cannot be edited directly. Duplicate first.")
    fields = _parse_fields(fields_json)
    item.name = name
    item.description = description or None
    item.industry = industry or None
    item.status = status
    item.fields_json = json.dumps(fields)
    item.updated_at = datetime.utcnow()
    if bump_version:
        item.version += 1
    db.commit()
    return _r(f"/superadmin/library/submodules/{item_id}?msg=saved")


@router.post("/submodules/{item_id}/duplicate")
def lib_sub_duplicate(item_id: str, sa: SuperAdmin = Depends(get_current_sa),
                       db: Session = Depends(get_db)):
    src = _get_sub(db, item_id)
    copy = LibrarySubmoduleDefinition(
        name=f"{src.name} (Copy)", description=src.description,
        industry=src.industry, status="DRAFT", is_system=False,
        fields_json=src.fields_json, created_by=sa.id,
    )
    db.add(copy); db.commit()
    return _r(f"/superadmin/library/submodules/{copy.id}?msg=duplicated")


@router.post("/submodules/{item_id}/deploy")
def lib_sub_deploy(item_id: str, tenant_id: str = Form(...),
                    notes: str = Form(""),
                    sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    item = _get_sub(db, item_id)
    tenant = db.query(Tenant).get(tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    _upsert_deployed(db, tenant_id, "submodule", item_id, item.name, item.version,
                     sa.id, notes)
    db.commit()
    return _r(f"/superadmin/library/submodules/{item_id}?msg=deployed")


@router.post("/submodules/{item_id}/bulk-push")
def lib_sub_bulk_push(item_id: str, sa: SuperAdmin = Depends(get_current_sa),
                       db: Session = Depends(get_db)):
    item = _get_sub(db, item_id)
    rows = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == item_id,
        TenantDeployedItem.item_type == "submodule",
    ).all()
    for r in rows:
        r.deployed_version = item.version
        r.deployed_at = datetime.utcnow()
        r.deployed_by = sa.id
    db.commit()
    return _r(f"/superadmin/library/submodules/{item_id}?msg=bulk_pushed")


@router.post("/submodules/{item_id}/revoke")
def lib_sub_revoke(item_id: str, tenant_id: str = Form(...),
                    sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    """Remove a sub-module deployment from a specific tenant."""
    row = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == item_id,
        TenantDeployedItem.tenant_id == tenant_id,
        TenantDeployedItem.item_type == "submodule",
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return _r(f"/superadmin/library/submodules/{item_id}?msg=revoked")


# ═══════════════════════════════════════════════════════════════════════════════
# CHECKLISTS (0-K-4)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/checklists", response_class=HTMLResponse)
def lib_checklists(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    items = db.query(LibraryChecklistTemplate).order_by(
        LibraryChecklistTemplate.status, LibraryChecklistTemplate.name).all()
    return templates.TemplateResponse(request, "superadmin/library_checklists.html",
        _lib_ctx(sa, "checklists", items=items))


@router.get("/checklists/new", response_class=HTMLResponse)
def lib_cl_new_page(request: Request, sa: SuperAdmin = Depends(get_current_sa)):
    return templates.TemplateResponse(request, "superadmin/library_checklist_edit.html",
        _lib_ctx(sa, "checklists", item=None, error=None,
                 status_options=STATUS_OPTIONS,
                 freq_options=["DAILY", "WEEKLY", "MONTHLY", "PER_SHIFT"],
                 role_options=["EMPLOYEE", "MANAGER", "ADMIN"]))


@router.post("/checklists/new")
def lib_cl_create(
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""), frequency: str = Form("DAILY"),
    assigned_to_role: str = Form("EMPLOYEE"),
    proof_required: bool = Form(False),
    status: str = Form("DRAFT"), notes: str = Form(""),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    ct = LibraryChecklistTemplate(
        name=name, description=description or None,
        industry=industry or None, frequency=frequency,
        assigned_to_role=assigned_to_role, proof_required=proof_required,
        status=status, notes=notes or None, is_system=False, created_by=sa.id,
    )
    db.add(ct); db.commit()
    return _r(f"/superadmin/library/checklists/{ct.id}?msg=created")


@router.get("/checklists/{item_id}", response_class=HTMLResponse)
def lib_cl_edit_page(item_id: str, request: Request,
                      sa: SuperAdmin = Depends(get_current_sa),
                      db: Session = Depends(get_db)):
    item = _get_cl(db, item_id)
    deployments = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == item_id,
        TenantDeployedItem.item_type == "checklist",
    ).all()
    tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
    deployed_tenant_ids = {d.tenant_id for d in deployments}
    cap_info = _checklist_cap_info(db, tenants)
    return templates.TemplateResponse(request, "superadmin/library_checklist_edit.html",
        _lib_ctx(sa, "checklists", item=item, error=None,
                 status_options=STATUS_OPTIONS,
                 freq_options=["DAILY", "WEEKLY", "MONTHLY", "PER_SHIFT"],
                 role_options=["EMPLOYEE", "MANAGER", "ADMIN"],
                 deployments=deployments, tenants=tenants,
                 deployed_tenant_ids=deployed_tenant_ids,
                 cap_info=cap_info,
                 msg=request.query_params.get("msg", "")))


@router.post("/checklists/{item_id}")
def lib_cl_save(
    item_id: str,
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""), frequency: str = Form("DAILY"),
    assigned_to_role: str = Form("EMPLOYEE"),
    proof_required: bool = Form(False),
    status: str = Form("DRAFT"), notes: str = Form(""),
    bump_version: bool = Form(False),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    item = _get_cl(db, item_id)
    if item.is_system:
        raise HTTPException(403, "System checklists cannot be edited. Duplicate first.")
    item.name = name; item.description = description or None
    item.industry = industry or None; item.frequency = frequency
    item.assigned_to_role = assigned_to_role; item.proof_required = proof_required
    item.status = status; item.notes = notes or None
    item.updated_at = datetime.utcnow()
    if bump_version:
        item.version += 1
    db.commit()
    return _r(f"/superadmin/library/checklists/{item_id}?msg=saved")


@router.post("/checklists/{item_id}/duplicate")
def lib_cl_duplicate(item_id: str, sa: SuperAdmin = Depends(get_current_sa),
                      db: Session = Depends(get_db)):
    src = _get_cl(db, item_id)
    copy = LibraryChecklistTemplate(
        name=f"{src.name} (Copy)", description=src.description,
        industry=src.industry, frequency=src.frequency,
        assigned_to_role=src.assigned_to_role, proof_required=src.proof_required,
        status="DRAFT", notes=src.notes, is_system=False, created_by=sa.id,
    )
    db.add(copy); db.commit()
    return _r(f"/superadmin/library/checklists/{copy.id}?msg=duplicated")


@router.post("/checklists/{item_id}/deploy")
def lib_cl_deploy(item_id: str, tenant_id: str = Form(...),
                   notes: str = Form(""),
                   sa: SuperAdmin = Depends(get_current_sa),
                   db: Session = Depends(get_db)):
    """Deploy = copy the library checklist into the tenant's actual checklist_templates table."""
    item = _get_cl(db, item_id)
    tenant = db.query(Tenant).get(tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    # Check cap only for first-time deploys (re-deploy is an update, always allowed)
    already_deployed = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.tenant_id == tenant_id,
        TenantDeployedItem.library_item_id == item_id,
        TenantDeployedItem.item_type == "checklist",
    ).first()
    if not already_deployed:
        cap = _checklist_cap_info(db, [tenant])[tenant_id]
        if cap["at_cap"]:
            limit = cap["limit"]
            current = cap["current"]
            return _r(
                f"/superadmin/library/checklists/{item_id}"
                f"?err=Cannot+deploy+to+{tenant.name}+%E2%80%94+"
                f"they+are+on+the+{cap['plan_label']}+plan+which+allows+"
                f"{limit}+checklist+template{'s' if limit != 1 else ''}+and+already+have+{current}."
            )
    # Create actual ChecklistTemplate for the tenant
    db.add(ChecklistTemplate(
        tenant_id=tenant_id, title=item.name,
        description=item.description or item.name,
        frequency=item.frequency,
        assigned_to_role=item.assigned_to_role,
        proof_required=item.proof_required,
    ))
    _upsert_deployed(db, tenant_id, "checklist", item_id, item.name, item.version,
                     sa.id, notes)
    db.commit()
    return _r(f"/superadmin/library/checklists/{item_id}?msg=deployed")


@router.post("/checklists/{item_id}/bulk-push")
def lib_cl_bulk_push(item_id: str, sa: SuperAdmin = Depends(get_current_sa),
                      db: Session = Depends(get_db)):
    item = _get_cl(db, item_id)
    rows = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == item_id,
        TenantDeployedItem.item_type == "checklist",
    ).all()
    for r in rows:
        r.deployed_version = item.version
        r.deployed_at = datetime.utcnow(); r.deployed_by = sa.id
    db.commit()
    return _r(f"/superadmin/library/checklists/{item_id}?msg=bulk_pushed")


# ═══════════════════════════════════════════════════════════════════════════════
# LABEL BUNDLES (0-K-5)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/labels", response_class=HTMLResponse)
def lib_labels(request: Request, sa: SuperAdmin = Depends(get_current_sa),
               db: Session = Depends(get_db)):
    bundles = db.query(LibraryLabelBundle).order_by(
        LibraryLabelBundle.industry, LibraryLabelBundle.name).all()
    tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
    return templates.TemplateResponse(request, "superadmin/library_labels.html",
        _lib_ctx(sa, "labels", bundles=bundles, tenants=tenants,
                 msg=request.query_params.get("msg", "")))


@router.get("/labels/new", response_class=HTMLResponse)
def lib_label_new_page(request: Request, sa: SuperAdmin = Depends(get_current_sa)):
    return templates.TemplateResponse(request, "superadmin/library_label_edit.html",
        _lib_ctx(sa, "labels", bundle=None, error=None))


@router.post("/labels/new")
def lib_label_create(
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""),
    ticket_s: str = Form(""), ticket_p: str = Form(""),
    checklist_s: str = Form(""), checklist_p: str = Form(""),
    branch_s: str = Form(""), branch_p: str = Form(""),
    department_s: str = Form(""), department_p: str = Form(""),
    employee_s: str = Form(""), employee_p: str = Form(""),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    lb = LibraryLabelBundle(
        name=name, description=description or None, industry=industry or None,
        is_system=False,
        ticket_s=ticket_s or None,    ticket_p=ticket_p or None,
        checklist_s=checklist_s or None, checklist_p=checklist_p or None,
        branch_s=branch_s or None,    branch_p=branch_p or None,
        department_s=department_s or None, department_p=department_p or None,
        employee_s=employee_s or None, employee_p=employee_p or None,
    )
    db.add(lb); db.commit()
    return _r(f"/superadmin/library/labels?msg=created")


@router.get("/labels/{bundle_id}/edit", response_class=HTMLResponse)
def lib_label_edit_page(bundle_id: str, request: Request,
                         sa: SuperAdmin = Depends(get_current_sa),
                         db: Session = Depends(get_db)):
    bundle = _get_lb(db, bundle_id)
    return templates.TemplateResponse(request, "superadmin/library_label_edit.html",
        _lib_ctx(sa, "labels", bundle=bundle, error=None))


@router.post("/labels/{bundle_id}/edit")
def lib_label_save(
    bundle_id: str,
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""),
    ticket_s: str = Form(""), ticket_p: str = Form(""),
    checklist_s: str = Form(""), checklist_p: str = Form(""),
    branch_s: str = Form(""), branch_p: str = Form(""),
    department_s: str = Form(""), department_p: str = Form(""),
    employee_s: str = Form(""), employee_p: str = Form(""),
    bump_version: bool = Form(False),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    bundle = _get_lb(db, bundle_id)
    bundle.name=name; bundle.description=description or None
    bundle.industry=industry or None
    bundle.ticket_s=ticket_s or None;    bundle.ticket_p=ticket_p or None
    bundle.checklist_s=checklist_s or None; bundle.checklist_p=checklist_p or None
    bundle.branch_s=branch_s or None;    bundle.branch_p=branch_p or None
    bundle.department_s=department_s or None; bundle.department_p=department_p or None
    bundle.employee_s=employee_s or None; bundle.employee_p=employee_p or None
    bundle.updated_at = datetime.utcnow()
    if bump_version:
        bundle.version += 1
    db.commit()
    return _r(f"/superadmin/library/labels?msg=saved")


@router.post("/labels/{bundle_id}/deploy")
def lib_label_deploy(bundle_id: str, tenant_id: str = Form(...),
                      sa: SuperAdmin = Depends(get_current_sa),
                      db: Session = Depends(get_db)):
    """Apply label bundle to tenant — writes TenantLabelConfig."""
    bundle = _get_lb(db, bundle_id)
    tenant = db.query(Tenant).get(tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    _apply_label_bundle(db, bundle, tenant_id)
    _upsert_deployed(db, tenant_id, "label_bundle", bundle_id, bundle.name, bundle.version,
                     sa.id, "Applied from label bundle library")
    db.commit()
    return _r(f"/superadmin/library/labels?msg=deployed")


@router.post("/labels/{bundle_id}/bulk-push")
def lib_label_bulk_push(bundle_id: str, sa: SuperAdmin = Depends(get_current_sa),
                         db: Session = Depends(get_db)):
    bundle = _get_lb(db, bundle_id)
    rows = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.library_item_id == bundle_id,
        TenantDeployedItem.item_type == "label_bundle",
    ).all()
    for r in rows:
        _apply_label_bundle(db, bundle, r.tenant_id)
        r.deployed_version = bundle.version
        r.deployed_at = datetime.utcnow(); r.deployed_by = sa.id
    db.commit()
    return _r(f"/superadmin/library/labels?msg=bulk_pushed")


# ═══════════════════════════════════════════════════════════════════════════════
# ONBOARDING BUNDLES (0-K-6)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/onboarding", response_class=HTMLResponse)
def lib_onboarding(request: Request, sa: SuperAdmin = Depends(get_current_sa),
                    db: Session = Depends(get_db)):
    bundles = db.query(LibraryOnboardingBundle).order_by(
        LibraryOnboardingBundle.industry, LibraryOnboardingBundle.name).all()
    tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
    label_bundles = db.query(LibraryLabelBundle).all()
    checklists = db.query(LibraryChecklistTemplate).filter(
        LibraryChecklistTemplate.status == "ACTIVE").all()
    flows = db.query(LibraryFlowTemplate).filter(
        LibraryFlowTemplate.status == "ACTIVE").all()
    submodules = db.query(LibrarySubmoduleDefinition).filter(
        LibrarySubmoduleDefinition.status == "ACTIVE").all()
    return templates.TemplateResponse(request, "superadmin/library_onboarding.html",
        _lib_ctx(sa, "onboarding", bundles=bundles, tenants=tenants,
                 label_bundles=label_bundles, checklists=checklists,
                 flows=flows, submodules=submodules,
                 msg=request.query_params.get("msg", "")))


@router.post("/onboarding/new")
def lib_ob_create(
    name: str = Form(...), description: str = Form(""),
    industry: str = Form(""), notes: str = Form(""),
    label_bundle_id: str = Form(""),
    checklist_ids: list[str] = Form(default=[]),
    flow_ids: list[str] = Form(default=[]),
    submodule_ids: list[str] = Form(default=[]),
    sa: SuperAdmin = Depends(get_current_sa), db: Session = Depends(get_db),
):
    ob = LibraryOnboardingBundle(
        name=name, description=description or None,
        industry=industry or None, notes=notes or None,
        is_system=False,
        label_bundle_id=label_bundle_id or None,
        checklist_ids_json=json.dumps(checklist_ids),
        flow_template_ids_json=json.dumps(flow_ids),
        submodule_ids_json=json.dumps(submodule_ids),
    )
    db.add(ob); db.commit()
    return _r(f"/superadmin/library/onboarding?msg=created")


@router.post("/onboarding/{bundle_id}/deploy")
def lib_ob_deploy(bundle_id: str, tenant_id: str = Form(...),
                   sa: SuperAdmin = Depends(get_current_sa),
                   db: Session = Depends(get_db)):
    """Deploy full onboarding bundle to a tenant — the 'one-click provision'."""
    ob = _get_ob(db, bundle_id)
    tenant = db.query(Tenant).get(tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    # 1. Apply label bundle
    if ob.label_bundle_id:
        bundle = db.query(LibraryLabelBundle).get(ob.label_bundle_id)
        if bundle:
            _apply_label_bundle(db, bundle, tenant_id)
            _upsert_deployed(db, tenant_id, "label_bundle", bundle.id,
                             bundle.name, bundle.version, sa.id,
                             f"Via onboarding bundle: {ob.name}")

    # 2. Deploy checklists
    for cl_id in json.loads(ob.checklist_ids_json or "[]"):
        cl = db.query(LibraryChecklistTemplate).get(cl_id)
        if cl:
            db.add(ChecklistTemplate(
                tenant_id=tenant_id, title=cl.name,
                description=cl.description or cl.name,
                frequency=cl.frequency,
                assigned_to_role=cl.assigned_to_role,
                proof_required=cl.proof_required,
            ))
            _upsert_deployed(db, tenant_id, "checklist", cl_id,
                             cl.name, cl.version, sa.id,
                             f"Via onboarding bundle: {ob.name}")

    # 3. Mark flow templates as deployed (FMS will use them in Phase 2)
    for ft_id in json.loads(ob.flow_template_ids_json or "[]"):
        ft = db.query(LibraryFlowTemplate).get(ft_id)
        if ft:
            _upsert_deployed(db, tenant_id, "flow", ft_id,
                             ft.name, ft.version, sa.id,
                             f"Via onboarding bundle: {ob.name}")

    # 4. Mark sub-modules as deployed
    for sm_id in json.loads(ob.submodule_ids_json or "[]"):
        sm = db.query(LibrarySubmoduleDefinition).get(sm_id)
        if sm:
            _upsert_deployed(db, tenant_id, "submodule", sm_id,
                             sm.name, sm.version, sa.id,
                             f"Via onboarding bundle: {ob.name}")

    # 5. Record the bundle itself as deployed
    _upsert_deployed(db, tenant_id, "onboarding_bundle", bundle_id,
                     ob.name, ob.version, sa.id, "Onboarding bundle deployed")
    db.commit()
    return _r(f"/superadmin/library/onboarding?msg=deployed")


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_flow(db, fid): return _get_or_404(db, LibraryFlowTemplate, fid, "Flow")
def _get_sub(db, sid):  return _get_or_404(db, LibrarySubmoduleDefinition, sid, "Sub-module")
def _get_cl(db, cid):   return _get_or_404(db, LibraryChecklistTemplate, cid, "Checklist")
def _get_lb(db, lid):   return _get_or_404(db, LibraryLabelBundle, lid, "Label bundle")
def _get_ob(db, oid):   return _get_or_404(db, LibraryOnboardingBundle, oid, "Onboarding bundle")

def _get_or_404(db, model, item_id: str, label: str):
    item = db.query(model).filter(model.id == item_id).first()
    if not item:
        raise HTTPException(404, f"{label} not found")
    return item


def _stage_to_dict(s: LibraryFlowStage) -> dict:
    return {
        "id": s.id, "name": s.name,
        "description": s.description or "",
        "color": s.color, "order": s.order, "is_terminal": s.is_terminal,
        "target_tat_hours": s.target_tat_hours or "",
        "sub_module_tag": s.sub_module_tag or "",
        "submodule_id": s.submodule_id or "",
        "completion_note_required": bool(s.completion_note_required),
        "evidence_required": bool(s.evidence_required),
    }


def _save_stages(db, template_id: str, stages_json: str):
    try:
        stages = json.loads(stages_json)
    except (json.JSONDecodeError, TypeError):
        stages = []
    for i, s in enumerate(stages):
        tat = s.get("target_tat_hours")
        db.add(LibraryFlowStage(
            template_id=template_id,
            name=(s.get("name") or "Stage").strip(),
            description=s.get("description") or None,
            color=s.get("color") or "#3b82f6",
            order=i,
            is_terminal=bool(s.get("is_terminal")),
            target_tat_hours=int(tat) if tat not in (None, "", 0, "0") else None,
            sub_module_tag=s.get("sub_module_tag") or None,
            submodule_id=s.get("submodule_id") or None,
            completion_note_required=bool(s.get("completion_note_required")),
            evidence_required=bool(s.get("evidence_required")),
        ))


def _parse_fields(fields_json: str) -> list:
    try:
        fields = json.loads(fields_json)
        if not isinstance(fields, list):
            return []
        cleaned = []
        for i, f in enumerate(fields):
            if not isinstance(f, dict) or not f.get("label"):
                continue
            cleaned.append({
                "id": f.get("id") or new_id(),
                "label": str(f["label"]).strip(),
                "type": f.get("type", "text"),
                "required": bool(f.get("required", False)),
                "options": f.get("options", []),
                "order": i,
            })
        return cleaned
    except (json.JSONDecodeError, TypeError):
        return []


def _apply_label_bundle(db, bundle: LibraryLabelBundle, tenant_id: str):
    """Write a label bundle into TenantLabelConfig."""
    row = db.query(TenantLabelConfig).filter(
        TenantLabelConfig.tenant_id == tenant_id).first()
    if row is None:
        row = TenantLabelConfig(tenant_id=tenant_id)
        db.add(row)
    row.ticket_s=bundle.ticket_s;      row.ticket_p=bundle.ticket_p
    row.checklist_s=bundle.checklist_s; row.checklist_p=bundle.checklist_p
    row.branch_s=bundle.branch_s;      row.branch_p=bundle.branch_p
    row.department_s=bundle.department_s; row.department_p=bundle.department_p
    row.employee_s=bundle.employee_s;  row.employee_p=bundle.employee_p
    row.industry=bundle.industry; row.updated_at=datetime.utcnow()


def _upsert_deployed(db, tenant_id: str, item_type: str, library_item_id: str,
                     item_name: str, version: int, sa_id: str, notes: str = ""):
    """Create or update a TenantDeployedItem record."""
    row = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.tenant_id == tenant_id,
        TenantDeployedItem.item_type == item_type,
        TenantDeployedItem.library_item_id == library_item_id,
    ).first()
    if row is None:
        row = TenantDeployedItem(
            tenant_id=tenant_id, item_type=item_type,
            library_item_id=library_item_id,
        )
        db.add(row)
    row.item_name = item_name
    row.deployed_version = version
    row.deployed_at = datetime.utcnow()
    row.deployed_by = sa_id
    row.notes = notes or None


def get_deployed_items_for_tenant(db, tenant_id: str) -> list[dict]:
    """Return deployed items with update-available flag (0-K-8)."""
    rows = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.tenant_id == tenant_id).all()
    result = []
    for r in rows:
        current_version = _get_library_version(db, r.item_type, r.library_item_id)
        result.append({
            "record": r,
            "update_available": (current_version is not None and
                                 current_version > r.deployed_version),
            "current_library_version": current_version,
        })
    return result


def _get_library_version(db, item_type: str, item_id: str) -> int | None:
    mapping = {
        "flow":             LibraryFlowTemplate,
        "submodule":        LibrarySubmoduleDefinition,
        "checklist":        LibraryChecklistTemplate,
        "label_bundle":     LibraryLabelBundle,
        "onboarding_bundle":LibraryOnboardingBundle,
    }
    model = mapping.get(item_type)
    if model is None:
        return None
    item = db.query(model).filter(model.id == item_id).first()
    return item.version if item else None
