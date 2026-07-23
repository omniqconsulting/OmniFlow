"""Setup — Performance Formula, Day-Status (Attendance) Rules, and FMS Flows
for the native app. Performance formula and attendance rules write to the
exact same tables/columns the website's /setup/performance-formula and
attendance-rule-engine (app/attendance_rules.py) read — so a formula or rule
saved from the app takes effect in employee performance scores and the
attendance calendar immediately, same as saving on the website. FMS Flows
here is read + active/inactive toggle only: the flow builder (stages,
routing, custom fields) is desktop-only, matching the design's own scope.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..attendance_rules import FIELD_CATALOG, OPERATORS_BY_KIND
from ..database import AttendanceRule, FMSFlow, PerformanceFormula, User, get_db
from .security import get_current_api_user

router = APIRouter(prefix="/setup", tags=["Setup"])


def _require_admin_or_pm(user: User = Depends(get_current_api_user)) -> User:
    if user.role not in ("ADMIN", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Admin or Product Manager only")
    return user


# ── Performance Formula ─────────────────────────────────────────────────

_PERF_KPI_KEYS = ["ticket_on_time", "ticket_completion", "checklist_compliance", "checklist_on_time", "fms_on_time"]
_PERF_DEFAULT_WEIGHTS = {"ticket_on_time": 40, "ticket_completion": 10, "checklist_compliance": 30, "checklist_on_time": 10, "fms_on_time": 10}
_PERF_LABELS = {
    "ticket_on_time": "Ticket On-Time Rate",
    "ticket_completion": "Ticket Completion Rate",
    "checklist_compliance": "Checklist Compliance",
    "checklist_on_time": "Checklist On-Time Rate",
    "fms_on_time": "FMS On-Time Rate",
}


class PerfComponentOut(BaseModel):
    key: str
    label: str
    weight: int


class PerfFormulaOut(BaseModel):
    label: Optional[str] = None
    components: list[PerfComponentOut]


@router.get("/performance", response_model=PerfFormulaOut)
def get_performance_formula(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    active = (
        db.query(PerformanceFormula)
        .filter(PerformanceFormula.tenant_id == user.tenant_id, PerformanceFormula.is_active == True)
        .order_by(PerformanceFormula.created_at.desc())
        .first()
    )
    weights = active.weights if active and active.weights else dict(_PERF_DEFAULT_WEIGHTS)
    return PerfFormulaOut(
        label=active.label if active else None,
        components=[PerfComponentOut(key=k, label=_PERF_LABELS[k], weight=int(weights.get(k, 0))) for k in _PERF_KPI_KEYS],
    )


class PerfFormulaIn(BaseModel):
    label: Optional[str] = None
    weights: dict[str, int]


@router.put("/performance", response_model=PerfFormulaOut)
def save_performance_formula(payload: PerfFormulaIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    weights = {k: max(0, min(100, int(payload.weights.get(k, 0)))) for k in _PERF_KPI_KEYS}
    db.query(PerformanceFormula).filter(
        PerformanceFormula.tenant_id == user.tenant_id, PerformanceFormula.is_active == True,
    ).update({"is_active": False})
    row = PerformanceFormula(
        tenant_id=user.tenant_id, label=(payload.label or "").strip() or None,
        weights=weights, is_active=True, created_by_id=user.id,
    )
    db.add(row)
    db.commit()
    return PerfFormulaOut(
        label=row.label,
        components=[PerfComponentOut(key=k, label=_PERF_LABELS[k], weight=weights[k]) for k in _PERF_KPI_KEYS],
    )


# ── Day-Status Rules ─────────────────────────────────────────────────────

class ConditionOut(BaseModel):
    field: str
    operator: str
    value: str


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    is_active: bool
    priority: int
    condition_logic: str
    outcome: str
    conditions: list[ConditionOut]


class ConditionIn(BaseModel):
    field: str
    operator: str
    value: str


class RuleIn(BaseModel):
    name: str
    is_active: bool = True
    priority: int = 0
    condition_logic: str = "ALL"
    outcome: str
    conditions: list[ConditionIn]


class FieldCatalogOut(BaseModel):
    field: str
    kind: str
    operators: list[str]


def _rule_out(r: AttendanceRule) -> RuleOut:
    try:
        conditions = json.loads(r.conditions_json) if r.conditions_json else []
    except (ValueError, TypeError):
        conditions = []
    return RuleOut(
        id=r.id, name=r.name, is_active=r.is_active, priority=r.priority,
        condition_logic=r.condition_logic or "ALL", outcome=r.outcome,
        conditions=[ConditionOut(**c) for c in conditions],
    )


def _validate_conditions(conditions: list[ConditionIn]) -> None:
    if not conditions:
        raise HTTPException(status_code=422, detail="At least one condition is required")
    for c in conditions:
        kind_extractor = FIELD_CATALOG.get(c.field)
        if not kind_extractor:
            raise HTTPException(status_code=422, detail=f"Unknown field: {c.field}")
        kind = kind_extractor[0]
        if c.operator not in OPERATORS_BY_KIND.get(kind, set()):
            raise HTTPException(status_code=422, detail=f"Invalid operator {c.operator} for {c.field}")


@router.get("/day-status-rules/fields", response_model=list[FieldCatalogOut])
def day_status_field_catalog(user: User = Depends(_require_admin_or_pm)):
    return [FieldCatalogOut(field=f, kind=kind, operators=sorted(OPERATORS_BY_KIND[kind])) for f, (kind, _extractor) in FIELD_CATALOG.items()]


@router.get("/day-status-rules", response_model=list[RuleOut])
def list_day_status_rules(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    rows = db.query(AttendanceRule).filter(AttendanceRule.tenant_id == user.tenant_id).order_by(AttendanceRule.priority).all()
    return [_rule_out(r) for r in rows]


@router.post("/day-status-rules", response_model=RuleOut)
def create_day_status_rule(payload: RuleIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    if payload.outcome not in ("PRESENT", "HALF_DAY", "ABSENT"):
        raise HTTPException(status_code=422, detail="Outcome must be PRESENT, HALF_DAY or ABSENT")
    _validate_conditions(payload.conditions)
    row = AttendanceRule(
        tenant_id=user.tenant_id, name=payload.name.strip(), is_active=payload.is_active, priority=payload.priority,
        condition_logic=payload.condition_logic if payload.condition_logic in ("ALL", "ANY") else "ALL",
        outcome=payload.outcome,
        conditions_json=json.dumps([c.model_dump() for c in payload.conditions]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _rule_out(row)


@router.put("/day-status-rules/{rule_id}", response_model=RuleOut)
def update_day_status_rule(rule_id: str, payload: RuleIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(AttendanceRule).filter(AttendanceRule.id == rule_id, AttendanceRule.tenant_id == user.tenant_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    if payload.outcome not in ("PRESENT", "HALF_DAY", "ABSENT"):
        raise HTTPException(status_code=422, detail="Outcome must be PRESENT, HALF_DAY or ABSENT")
    _validate_conditions(payload.conditions)
    row.name = payload.name.strip()
    row.is_active = payload.is_active
    row.priority = payload.priority
    row.condition_logic = payload.condition_logic if payload.condition_logic in ("ALL", "ANY") else "ALL"
    row.outcome = payload.outcome
    row.conditions_json = json.dumps([c.model_dump() for c in payload.conditions])
    db.commit()
    db.refresh(row)
    return _rule_out(row)


@router.delete("/day-status-rules/{rule_id}", status_code=204)
def delete_day_status_rule(rule_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(AttendanceRule).filter(AttendanceRule.id == rule_id, AttendanceRule.tenant_id == user.tenant_id).first()
    if row:
        db.delete(row)
        db.commit()
    return None


# ── FMS Flows (read + active toggle only — builder stays desktop-only) ───

class FlowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = None
    color: str
    is_active: bool
    stage_count: int


@router.get("/flows", response_model=list[FlowOut])
def list_flows(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    rows = db.query(FMSFlow).filter(FMSFlow.tenant_id == user.tenant_id, FMSFlow.is_deleted == False).order_by(FMSFlow.name).all()
    return [
        FlowOut(id=f.id, name=f.name, description=f.description, color=f.color, is_active=f.is_active, stage_count=len(f.stages))
        for f in rows
    ]


class FlowActiveIn(BaseModel):
    is_active: bool


@router.put("/flows/{flow_id}/active", response_model=FlowOut)
def set_flow_active(flow_id: str, payload: FlowActiveIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    f = db.query(FMSFlow).filter(FMSFlow.id == flow_id, FMSFlow.tenant_id == user.tenant_id, FMSFlow.is_deleted == False).first()
    if not f:
        raise HTTPException(status_code=404, detail="Flow not found")
    f.is_active = payload.is_active
    db.commit()
    db.refresh(f)
    return FlowOut(id=f.id, name=f.name, description=f.description, color=f.color, is_active=f.is_active, stage_count=len(f.stages))
