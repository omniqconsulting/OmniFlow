"""Operations Dashboard — native app.

Wraps the same KPI/score calculation helpers the desktop Operations
Dashboard (app/main.py's /dashboard route) already uses, so numbers match
exactly across web and native. No desktop route/template is touched here —
this module only imports read-only helper functions from app.main /
app.constants at call time (same inline-import pattern api_v1/tasks.py
already uses to avoid a circular import with app.main at module load)."""
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import Tenant, Ticket, TicketAssignee, User, get_db
from .features import require_feature
from .security import get_current_api_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"], dependencies=[Depends(require_feature("TICKETS"))])


def _range_to_dates(range_key: str) -> tuple:
    today = date.today()
    if range_key == "today":
        d_from = today
    elif range_key == "7d":
        d_from = today - timedelta(days=7)
    elif range_key == "mtd":
        d_from = today.replace(day=1)
    else:  # "30d" default
        d_from = today - timedelta(days=30)
    return d_from.isoformat(), today.isoformat()


class PerfComponentOut(BaseModel):
    label: str
    value: int
    color: str
    weight: int


class TicketStatsOut(BaseModel):
    total: int
    open: int
    completed: int
    on_time_pct: int
    on_time_count: int
    issues_open: int


class ChecklistStatsOut(BaseModel):
    due: int
    completed: int
    compliance_pct: int
    on_time: int
    missed: int


class FmsStatsOut(BaseModel):
    total: int
    active: int
    completed: int
    on_time: int
    tat_breach: int


class PriorityTaskOut(BaseModel):
    id: str
    title: str
    assignee_name: Optional[str]
    due_at: Optional[datetime]
    overdue: bool


class DeptHealthOut(BaseModel):
    dept_id: str
    name: str
    rate: int


class DashboardSummaryOut(BaseModel):
    can_view: bool
    score: int = 0
    components: List[PerfComponentOut] = []
    tickets: Optional[TicketStatsOut] = None
    checklists: Optional[ChecklistStatsOut] = None
    fms: Optional[FmsStatsOut] = None
    priority_tasks: List[PriorityTaskOut] = []
    priority_tasks_count: int = 0
    dept_health: List[DeptHealthOut] = []


@router.get("/summary", response_model=DashboardSummaryOut)
def dashboard_summary(
    range: str = Query("30d"),
    dept_ids: List[str] = Query([]),
    manager_ids: List[str] = Query([]),
    branch_ids: List[str] = Query([]),
    include_dept_health: bool = Query(True),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    if user.role == "EMPLOYEE":
        return DashboardSummaryOut(can_view=False)

    tid = user.tenant_id
    tenant = db.query(Tenant).filter(Tenant.id == tid).first()

    # Managers are locked to their own team, same as desktop.
    _manager_ids = [user.id] if user.role == "MANAGER" else (manager_ids or None)

    date_from, date_to = _range_to_dates(range)

    from ..constants import has_feature
    from ..main import _calc_dept_health, _calc_summary_kpis, _compute_perf_score, _get_active_formula

    kpis = _calc_summary_kpis(db, tid, date_from, date_to, dept_ids or None, _manager_ids, None, branch_ids or None)
    has_fms = has_feature(tenant, "FMS", db) if tenant else False

    kpi_values = {
        "ticket_on_time": kpis.on_time_pct if kpis.total_count > 0 else None,
        "ticket_completion": round(kpis.closed_count / kpis.total_count * 100) if kpis.total_count > 0 else None,
        "checklist_compliance": kpis.cl_compliance_pct if kpis.cl_due > 0 else None,
        "checklist_on_time": round(kpis.cl_on_time / kpis.cl_done * 100) if getattr(kpis, "cl_done", 0) > 0 else None,
        "fms_on_time": round(kpis.fms_on_time / kpis.fms_completed * 100) if has_fms and kpis.fms_completed > 0 else None,
    }
    weights = _get_active_formula(db, tid)
    score, components = _compute_perf_score(kpi_values, weights)

    # Priority Tasks — CRITICAL priority, not closed, MANAGER scoped to their
    # team (reports + self + tickets they help on) — mirrors main.py's
    # dashboard "hot_tasks" query verbatim.
    hot_q = db.query(Ticket).filter(
        Ticket.tenant_id == tid, Ticket.is_deleted == False,
        Ticket.priority == "CRITICAL", Ticket.status != "CLOSED")
    if user.role == "MANAGER":
        mgr_team_ids = [u.id for u in db.query(User).filter(
            User.manager_id == user.id, User.is_deleted == False).all()]
        mgr_team_ids.append(user.id)
        mgr_helper_tids = [h.ticket_id for h in db.query(TicketAssignee).filter(
            TicketAssignee.user_id.in_(mgr_team_ids)).all()]
        hot_q = hot_q.filter(
            (Ticket.current_assignee_id.in_(mgr_team_ids)) |
            (Ticket.created_by_id.in_(mgr_team_ids)) |
            (Ticket.id.in_(mgr_helper_tids))
        )
    hot_tasks = hot_q.order_by(Ticket.priority.asc(), Ticket.due_at.asc().nullslast()).all()
    priority_tasks_count = len(hot_tasks)
    hot_tasks = hot_tasks[:10]
    now = datetime.utcnow()
    priority_tasks = [
        PriorityTaskOut(
            id=t.id, title=t.title,
            assignee_name=t.assignee_name,
            due_at=t.due_at,
            overdue=bool(t.due_at and t.due_at < now),
        )
        for t in hot_tasks
    ]

    dept_health = []
    if include_dept_health:
        dept_health = [DeptHealthOut(**d) for d in _calc_dept_health(db, tid, date_from, date_to)]

    fms_out = None
    if has_fms:
        fms_out = FmsStatsOut(
            total=kpis.fms_total, active=kpis.fms_active, completed=kpis.fms_completed,
            on_time=kpis.fms_on_time, tat_breach=kpis.fms_tat_breaches,
        )

    return DashboardSummaryOut(
        can_view=True,
        score=score,
        components=[PerfComponentOut(**c) for c in components],
        tickets=TicketStatsOut(
            total=kpis.total_count, open=kpis.total_open, completed=kpis.closed_count,
            on_time_pct=kpis.on_time_pct, on_time_count=kpis.on_time_count, issues_open=kpis.open_help,
        ),
        checklists=ChecklistStatsOut(
            due=kpis.cl_due, completed=kpis.cl_done, compliance_pct=kpis.cl_compliance_pct,
            on_time=kpis.cl_on_time, missed=kpis.cl_missed,
        ),
        fms=fms_out,
        priority_tasks=priority_tasks,
        priority_tasks_count=priority_tasks_count,
        dept_health=dept_health,
    )
