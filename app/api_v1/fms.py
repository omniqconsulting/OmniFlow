"""FMS Flow Board — native app.

Ports the mockup's feature surface (create/transition/comment/flag/reassign/
hold/help/close) onto JSON endpoints that reuse the desktop's fine-grained
FMS helper functions (app/fms.py) — never the desktop route handlers
themselves, which are monolithic HTML-form/redirect functions tightly
coupled to cookie auth and raw Request.form() parsing. Every helper is
imported inline inside the function body that needs it (same pattern
api_v1/checklists.py already uses for app/main.py helpers), so ticket state
stays byte-for-byte consistent with what the desktop app produces. No
desktop route/template is touched.

Splits are an internal FMS implementation detail the native UI never sees:
create always makes exactly one split (_init_first_split), every write
endpoint resolves the ticket's split via _ensure_ticket_has_split (idempotent
— returns the existing active split or creates one), split_id is never
accepted from the client.
"""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import (
    FMSEvent,
    FMSFlow,
    FMSStage,
    FMSStageHistory,
    FMSTicket,
    FMSTicketHelper,
    Tenant,
    User,
    get_db,
)
from .features import require_feature
from .pagination import paginate_cursor
from .schemas import Page, UtcDateTime
from .security import get_current_api_user

router = APIRouter(prefix="/fms", tags=["FMS"], dependencies=[Depends(require_feature("FMS"))])

# Mirrors app.constants.FMS_INACTIVE_STATUSES
FMS_INACTIVE_STATUSES = ("COMPLETED", "CLOSED")


# ── Role scoping (mirrors app/fms.py's _fms_dashboard_inner exactly) ────────

def _visible_flows(db: Session, user: User) -> List[FMSFlow]:
    tid = user.tenant_id
    all_flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tid, FMSFlow.is_active == True, FMSFlow.is_deleted == False,
    ).order_by(FMSFlow.created_at).all()
    if user.role == "ADMIN":
        return all_flows

    from ..fms import _stage_default_assignee_ids

    if user.role == "EMPLOYEE":
        flow_ids: set = set()
        for t in db.query(FMSTicket.flow_id).filter(
            FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
        ).filter(
            (FMSTicket.current_assignee_id == user.id) |
            FMSTicket.id.in_(db.query(FMSStageHistory.ticket_id).filter(FMSStageHistory.assignee_id == user.id)) |
            FMSTicket.stage_assignees_json.like(f'%"{user.id}"%')
        ).distinct():
            flow_ids.add(t.flow_id)
        for s in db.query(FMSStage.flow_id).filter(
            FMSStage.tenant_id == tid, FMSStage.is_deleted == False,
            (FMSStage.default_assignee_id == user.id) | FMSStage.default_assignee_ids_json.like(f'%"{user.id}"%'),
        ).distinct():
            flow_ids.add(s.flow_id)
        return [f for f in all_flows if f.id in flow_ids]

    # MANAGER
    team_ids = [u.id for u in db.query(User).filter(User.manager_id == user.id, User.is_deleted == False).all()]
    team_ids.append(user.id)
    flow_ids = set()
    for f in db.query(FMSFlow.id).filter(FMSFlow.created_by_id == user.id):
        flow_ids.add(f.id)
    for t in db.query(FMSTicket.flow_id).filter(
        FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
    ).filter(
        (FMSTicket.current_assignee_id.in_(team_ids)) |
        FMSTicket.id.in_(db.query(FMSStageHistory.ticket_id).filter(FMSStageHistory.assignee_id.in_(team_ids))) |
        FMSTicket.id.in_(db.query(FMSTicketHelper.ticket_id).filter(FMSTicketHelper.user_id.in_(team_ids)))
    ).distinct():
        flow_ids.add(t.flow_id)
    team_ids_set = set(team_ids)
    for s in db.query(FMSStage).filter(
        FMSStage.tenant_id == tid, FMSStage.is_deleted == False,
        (FMSStage.default_assignee_id.in_(team_ids)) | (FMSStage.default_assignee_ids_json != None),
    ):
        if s.default_assignee_id in team_ids_set or team_ids_set & set(_stage_default_assignee_ids(s)):
            flow_ids.add(s.flow_id)
    return [f for f in all_flows if f.id in flow_ids]


def _scoped_ticket_query(db: Session, user: User):
    tid = user.tenant_id
    q = db.query(FMSTicket).filter(FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False)
    if user.role == "ADMIN":
        return q
    if user.role == "MANAGER":
        team_ids = [u.id for u in db.query(User).filter(User.manager_id == user.id, User.is_deleted == False).all()]
        team_ids.append(user.id)
        hist_tids = [h.ticket_id for h in db.query(FMSStageHistory).filter(FMSStageHistory.assignee_id.in_(team_ids)).distinct().all()]
        help_tids = [h.ticket_id for h in db.query(FMSTicketHelper).filter(FMSTicketHelper.user_id.in_(team_ids)).all()]
        scoped_ids = set(hist_tids) | set(help_tids)
        return q.filter((FMSTicket.current_assignee_id.in_(team_ids)) | (FMSTicket.id.in_(scoped_ids)))
    # EMPLOYEE
    helper_tids = [h.ticket_id for h in db.query(FMSTicketHelper).filter(FMSTicketHelper.user_id == user.id).all()]
    hist_tids = [h.ticket_id for h in db.query(FMSStageHistory).filter(FMSStageHistory.assignee_id == user.id).all()]
    upcoming_tids = [t.id for t in db.query(FMSTicket).filter(
        FMSTicket.tenant_id == tid, FMSTicket.is_deleted == False,
        FMSTicket.stage_assignees_json.like(f'%"{user.id}"%')).all()]
    scoped_ids = set(helper_tids) | set(hist_tids) | set(upcoming_tids)
    return q.filter((FMSTicket.current_assignee_id == user.id) | (FMSTicket.id.in_(scoped_ids)))


def _compute_kpis(db: Session, tid: str, base_q) -> dict:
    from ..fms import _open_histories_for_ticket

    now = datetime.utcnow()
    active_tickets = base_q.filter(FMSTicket.status.notin_(FMS_INACTIVE_STATUSES)).count()
    flagged = base_q.filter(FMSTicket.is_flagged == True).count()
    awaiting_action = base_q.filter(FMSTicket.status == "ACTIVE").count()

    tat_breaches = 0
    open_tickets = base_q.filter(FMSTicket.status.notin_(FMS_INACTIVE_STATUSES)).all()
    for t in open_tickets:
        for h in _open_histories_for_ticket(db, t.id):
            stage_for_h = h.stage
            if h.planned_end:
                if now > h.planned_end:
                    tat_breaches += 1
            elif stage_for_h and stage_for_h.target_tat_hours:
                elapsed = (now - h.entered_at).total_seconds() / 3600
                if elapsed > stage_for_h.target_tat_hours:
                    tat_breaches += 1

    # Compliance is computed tenant-wide (not base_q-scoped) — matches desktop.
    all_history = db.query(FMSStageHistory).join(
        FMSTicket, FMSStageHistory.ticket_id == FMSTicket.id
    ).filter(FMSTicket.tenant_id == tid, FMSStageHistory.exited_at != None).all()
    scoreable = [h for h in all_history if (h.planned_start and h.planned_end) or (h.stage and h.stage.target_tat_hours)]
    if scoreable:
        def _on_time(h):
            actual_h = (h.exited_at - h.entered_at).total_seconds() / 3600
            if h.planned_start and h.planned_end:
                target_h = (h.planned_end - h.planned_start).total_seconds() / 3600
            else:
                target_h = h.stage.target_tat_hours
            return actual_h <= target_h
        compliance = int(sum(1 for h in scoreable if _on_time(h)) / len(scoreable) * 100)
    else:
        compliance = 0

    return {
        "active_tickets": active_tickets, "tat_breaches": tat_breaches,
        "flagged": flagged, "awaiting_action": awaiting_action, "compliance_pct": compliance,
    }


# ── Schemas ──────────────────────────────────────────────────────────────

class StageCustomFieldOut(BaseModel):
    id: str
    field_type: str
    label: str
    required: bool


class StageOut(BaseModel):
    id: str
    name: str
    order: int
    color: str
    target_tat_hours: Optional[float]
    is_terminal: bool
    evidence_required: bool
    completion_note_required: bool
    custom_fields: List[StageCustomFieldOut]
    has_linked_flow: bool = False


class FlowOut(BaseModel):
    id: str
    name: str
    color: str
    stages: List[StageOut]
    has_next_flow: bool = False


class EmployeeOut(BaseModel):
    id: str
    name: str


class FMSTicketOut(BaseModel):
    id: str
    display_id: Optional[str]
    flow_id: str
    flow_name: str
    current_stage_id: Optional[str]
    current_stage_name: Optional[str]
    current_stage_order: Optional[int]
    title: str
    status: str
    priority: str
    current_assignee_id: Optional[str]
    assignee_name: Optional[str]
    due_at: Optional[UtcDateTime]
    is_flagged: bool
    flagged_reason: Optional[str]
    created_at: UtcDateTime
    tat_pct: Optional[int]
    pause_reason: Optional[str] = None
    continued_from_ticket_id: Optional[str] = None
    continued_from_display_id: Optional[str] = None
    continued_to_ticket_id: Optional[str] = None
    continued_to_display_id: Optional[str] = None
    linked_child_ticket_id: Optional[str] = None
    linked_child_display_id: Optional[str] = None
    linked_parent_ticket_id: Optional[str] = None
    linked_parent_display_id: Optional[str] = None


class StageHistoryOut(BaseModel):
    stage_id: str
    stage_name: str
    entered_at: UtcDateTime
    exited_at: Optional[UtcDateTime]
    assignee_id: Optional[str]
    assignee_name: Optional[str]


class FMSTicketDetailOut(FMSTicketOut):
    stage_history: List[StageHistoryOut]


class BoardKPIsOut(BaseModel):
    active_tickets: int
    tat_breaches: int
    flagged: int
    awaiting_action: int
    compliance_pct: int


class BoardOut(BaseModel):
    kpis: BoardKPIsOut
    tickets: List[FMSTicketOut]


class TicketEventOut(BaseModel):
    event_type: str
    detail: str
    actor_name: str
    created_at: str


def _build_ticket_out(db: Session, t: FMSTicket) -> FMSTicketOut:
    from ..fms import _open_history, _tat_pct

    stage = t.current_stage
    assignee = t.current_assignee
    open_h = _open_history(db, t.id) if stage else None
    tat_pct = _tat_pct(open_h, stage) if open_h else None

    def _display_id(ticket_id):
        if not ticket_id:
            return None
        other = db.query(FMSTicket).get(ticket_id)
        return other.display_id if other else None

    return FMSTicketOut(
        id=t.id, display_id=t.display_id, flow_id=t.flow_id, flow_name=t.flow.name if t.flow else "",
        current_stage_id=t.current_stage_id, current_stage_name=stage.name if stage else None,
        current_stage_order=stage.order if stage else None,
        title=t.title, status=t.status, priority=t.priority,
        current_assignee_id=t.current_assignee_id, assignee_name=assignee.name if assignee else None,
        due_at=t.due_at, is_flagged=bool(t.is_flagged), flagged_reason=t.flagged_reason,
        created_at=t.created_at, tat_pct=tat_pct,
        pause_reason=t.pause_reason,
        continued_from_ticket_id=t.continued_from_ticket_id,
        continued_from_display_id=_display_id(t.continued_from_ticket_id),
        continued_to_ticket_id=t.continued_to_ticket_id,
        continued_to_display_id=_display_id(t.continued_to_ticket_id),
        linked_child_ticket_id=t.linked_child_ticket_id,
        linked_child_display_id=_display_id(t.linked_child_ticket_id),
        linked_parent_ticket_id=t.linked_parent_ticket_id,
        linked_parent_display_id=_display_id(t.linked_parent_ticket_id),
    )


def _build_ticket_detail_out(db: Session, t: FMSTicket) -> FMSTicketDetailOut:
    base = _build_ticket_out(db, t)
    hist_rows = db.query(FMSStageHistory).filter(
        FMSStageHistory.ticket_id == t.id
    ).order_by(FMSStageHistory.entered_at).all()
    hist_out = []
    for h in hist_rows:
        assignee = db.query(User).filter(User.id == h.assignee_id).first() if h.assignee_id else None
        hist_out.append(StageHistoryOut(
            stage_id=h.stage_id, stage_name=h.stage_name or "", entered_at=h.entered_at,
            exited_at=h.exited_at, assignee_id=h.assignee_id, assignee_name=assignee.name if assignee else None,
        ))
    return FMSTicketDetailOut(**base.model_dump(), stage_history=hist_out)


# ── Read endpoints ───────────────────────────────────────────────────────

@router.get("/flows", response_model=List[FlowOut])
def list_flows(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    flows = _visible_flows(db, user)
    out = []
    for f in flows:
        stages = sorted([s for s in f.stages if not s.is_deleted], key=lambda s: s.order)
        stage_outs = []
        for s in stages:
            try:
                cfs = json.loads(s.custom_fields_json or "[]")
            except Exception:
                cfs = []
            stage_outs.append(StageOut(
                id=s.id, name=s.name, order=s.order, color=s.color,
                target_tat_hours=s.target_tat_hours, is_terminal=bool(s.is_terminal),
                evidence_required=bool(s.evidence_required), completion_note_required=bool(s.completion_note_required),
                custom_fields=[
                    StageCustomFieldOut(id=c.get("id", ""), field_type=c.get("field_type", "text"),
                                         label=c.get("label", ""), required=bool(c.get("required")))
                    for c in cfs
                ],
                has_linked_flow=bool(s.linked_library_flow_id),
            ))
        out.append(FlowOut(id=f.id, name=f.name, color=f.color, stages=stage_outs, has_next_flow=bool(f.next_library_flow_id)))
    return out


@router.get("/employees", response_model=List[EmployeeOut])
def list_assignable_employees(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Unrestricted (any authenticated tenant role) — {id, name} only, needed
    so an Employee can pick an assignee when moving their own ticket forward."""
    rows = db.query(User).filter(
        User.tenant_id == user.tenant_id, User.is_deleted == False, User.is_active == True,
    ).order_by(User.name).all()
    return [EmployeeOut(id=u.id, name=u.name) for u in rows]


@router.get("/board", response_model=BoardOut)
def get_board(
    flow_id: Optional[str] = Query(None), my_work: int = Query(0),
    user: User = Depends(get_current_api_user), db: Session = Depends(get_db),
):
    tid = user.tenant_id
    base_q = _scoped_ticket_query(db, user)
    if flow_id:
        base_q = base_q.filter(FMSTicket.flow_id == flow_id)
    kpis = _compute_kpis(db, tid, base_q)

    tq = base_q
    if my_work:
        tq = tq.filter(FMSTicket.current_assignee_id == user.id)
    tickets = tq.order_by(FMSTicket.created_at.desc()).all()
    return BoardOut(kpis=BoardKPIsOut(**kpis), tickets=[_build_ticket_out(db, t) for t in tickets])


@router.get("/tickets", response_model=Page[FMSTicketOut])
def list_tickets(
    status: Optional[str] = Query(None), flow_id: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None), limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user), db: Session = Depends(get_db),
):
    q = _scoped_ticket_query(db, user)
    if status:
        q = q.filter(FMSTicket.status == status)
    if flow_id:
        q = q.filter(FMSTicket.flow_id == flow_id)
    rows, next_cursor = paginate_cursor(q, FMSTicket, cursor, limit)
    return Page(items=[_build_ticket_out(db, t) for t in rows], next_cursor=next_cursor)


@router.get("/tickets/{ticket_id}", response_model=FMSTicketDetailOut)
def get_ticket(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    from ..fms import _can_act_on_ticket, _get_ticket

    t = _get_ticket(db, ticket_id, user.tenant_id)
    if not _can_act_on_ticket(user, t, None):
        raise HTTPException(403, "Not authorised to view this ticket")
    return _build_ticket_detail_out(db, t)


@router.get("/tickets/{ticket_id}/events", response_model=List[TicketEventOut])
def get_ticket_events(ticket_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    from ..fms import _can_act_on_ticket, _get_ticket

    t = _get_ticket(db, ticket_id, user.tenant_id)
    if not _can_act_on_ticket(user, t, None):
        raise HTTPException(403, "Not authorised to view this ticket")
    events = db.query(FMSEvent).filter(FMSEvent.ticket_id == t.id).order_by(FMSEvent.created_at.desc()).all()
    actor_ids = {e.actor_id for e in events if e.actor_id}
    actors = {u.id: u.name for u in db.query(User).filter(User.id.in_(actor_ids)).all()} if actor_ids else {}
    return [
        TicketEventOut(
            event_type=e.event_type, detail=e.detail or "",
            actor_name=actors.get(e.actor_id, "System"),
            created_at=e.created_at.strftime("%d %b %Y, %H:%M") if e.created_at else "",
        )
        for e in events
    ]


# ── Write endpoints ──────────────────────────────────────────────────────

class CreateTicketIn(BaseModel):
    flow_id: str
    starting_stage_id: str
    title: str
    priority: str = "MEDIUM"
    assignee_id: str
    due_at: Optional[str] = None


@router.post("/tickets", response_model=FMSTicketOut)
def create_ticket(body: CreateTicketIn, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    from ..fms import _admin_ids, _can_create_in_flow, _get_flow, _init_first_split, _log, _manager_ids_for, _next_fms_display_id
    from ..notifications import notify_fms_stage_transition, notify_fms_ticket_opened, send_whatsapp_for_fms_ticket_created

    flow = _get_flow(db, body.flow_id, user.tenant_id)
    if not _can_create_in_flow(user, flow):
        raise HTTPException(403, "Not authorised to create tickets in this flow")
    stage = db.query(FMSStage).filter(FMSStage.id == body.starting_stage_id, FMSStage.flow_id == body.flow_id).first()
    if not stage:
        raise HTTPException(400, "Invalid starting stage")
    if not body.title.strip():
        raise HTTPException(400, "Title is required")

    due_dt = None
    if body.due_at:
        try:
            due_dt = datetime.fromisoformat(body.due_at)
        except ValueError:
            raise HTTPException(400, "Invalid due_at — expected ISO 8601")

    ticket = FMSTicket(
        tenant_id=user.tenant_id, flow_id=body.flow_id, current_stage_id=stage.id,
        title=body.title.strip(), priority=body.priority, current_assignee_id=body.assignee_id,
        due_at=due_dt, created_by_id=user.id, status="ACTIVE",
        stage_assignees_json=json.dumps({stage.id: body.assignee_id}),
    )
    db.add(ticket)
    db.flush()

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    ticket.display_id = _next_fms_display_id(db, tenant)
    split = _init_first_split(db, ticket, stage.id, body.assignee_id)
    db.add(FMSStageHistory(
        ticket_id=ticket.id, split_id=split.id, stage_id=stage.id, stage_name=stage.name,
        assignee_id=body.assignee_id, direction="FORWARD",
    ))
    assignee_obj = db.query(User).filter(User.id == body.assignee_id).first()
    _log(db, ticket.id, user.id, "CREATED",
         f"Title: {ticket.title} | Priority: {ticket.priority} | Assignee: {assignee_obj.name if assignee_obj else '—'}")
    _log(db, ticket.id, user.id, "STAGE_ENTERED", f"Stage: {stage.name}")
    db.commit()
    db.refresh(ticket)

    admins = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for(db, body.assignee_id)
    notify_fms_stage_transition(db, user.tenant_id, ticket.id, ticket.title, stage.name, user.id, admins, managers, body.assignee_id)
    if assignee_obj:
        send_whatsapp_for_fms_ticket_created(db, ticket, assignee_obj)
    notify_fms_ticket_opened(db, ticket, assignee_obj, admins, managers)

    return _build_ticket_out(db, ticket)


@router.post("/tickets/{ticket_id}/transition", response_model=FMSTicketDetailOut)
async def transition_ticket(
    ticket_id: str,
    next_stage_id: str = Form(""),
    new_assignee_id: str = Form(""),
    completion_note: str = Form(""),
    return_reason: str = Form(""),
    is_override: bool = Form(False),
    custom_field_values_json: str = Form("{}"),
    evidence_file: UploadFile = File(None),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    from ..fms import (
        _active_splits,
        _admin_ids,
        _can_act_on_ticket,
        _can_transition,
        _cross_stage_cf,
        _check_qty_discrepancy,
        _ensure_ticket_has_split,
        _evaluate_auto_split,
        _get_ticket,
        _log,
        _manager_ids_for,
        _mark_completed_by,
        _open_history,
        _split_lineage_ids,
        _stage_default_assignee,
        _sync_ticket_cache,
        _ticket_closing_rule_check,
    )
    from ..notifications import (
        create_notification,
        notify_fms_stage_transition,
        send_whatsapp_for_fms_ticket_closed,
    )
    from ..ws_manager import FMS_STAGE_TRANSITION, broadcast_sync

    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    split = _ensure_ticket_has_split(db, ticket)

    can_via_whitelist = (
        user.role == "EMPLOYEE" and ticket.flow and ticket.flow.restrict_to_assignee and
        _can_act_on_ticket(user, ticket, split)
    )
    if not (_can_transition(user, ticket, split) or can_via_whitelist):
        raise HTTPException(403, "Not authorised to transition this ticket")
    if ticket.status == "CLOSED" or split.status == "CLOSED":
        raise HTTPException(400, "This ticket is closed")
    if split.status == "COMPLETED" and not (is_override and user.role in ("ADMIN", "MANAGER")):
        raise HTTPException(400, "This ticket is already completed — a manager/admin override is required to reopen it")

    terminal_complete = not next_stage_id.strip()
    next_stage = None
    if not terminal_complete:
        next_stage = db.query(FMSStage).filter(FMSStage.id == next_stage_id, FMSStage.flow_id == ticket.flow_id).first()
        if not next_stage:
            raise HTTPException(400, "Invalid next stage")
        new_assignee_id = (new_assignee_id or "").strip()
        if not new_assignee_id:
            new_assignee_id = _stage_default_assignee(next_stage) or ""
        if not new_assignee_id:
            raise HTTPException(400, "Please select an assignee for the next stage")

    cur_stage = split.current_stage
    open_h = _open_history(db, ticket_id, split_id=split.id)
    cur_order = cur_stage.order if cur_stage else 0

    if terminal_complete:
        direction = "FORWARD"
        next_order = cur_order
    else:
        next_order = next_stage.order
        direction = "BACKWARD" if next_order < cur_order else "FORWARD"

    if not is_override and not terminal_complete:
        if direction == "FORWARD" and next_order != cur_order + 1:
            raise HTTPException(400, "Tickets can only move to the next stage in sequence")

    if is_override:
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Only managers/admins can override")
        direction = "MANAGER_OVERRIDE"

    if direction == "BACKWARD" and len(return_reason.strip()) < 5:
        raise HTTPException(400, "A valid return reason (at least 5 characters) is required to move a ticket back")

    if (cur_stage and cur_stage.completion_note_required and not completion_note.strip()
            and direction not in ("BACKWARD", "MANAGER_OVERRIDE")):
        raise HTTPException(400, f"Stage '{cur_stage.name}' requires a completion note")

    evidence_url = None
    evidence_filename = None
    if (cur_stage and getattr(cur_stage, "evidence_required", False)
            and direction not in ("BACKWARD", "MANAGER_OVERRIDE")):
        has_file = evidence_file is not None and evidence_file.filename and evidence_file.filename.strip()
        if not has_file:
            raise HTTPException(400, f"Stage '{cur_stage.name}' requires an evidence file upload before moving on")
        from ..uploads import save_upload
        result = await save_upload(evidence_file, user.tenant_id)
        evidence_url = result["file_path"]
        evidence_filename = result["file_name"]

    try:
        submitted_cf = json.loads(custom_field_values_json or "{}")
    except Exception:
        submitted_cf = {}

    custom_fields_data: dict = {}
    field_defs: list = []
    formula_lookup: dict = {}
    if cur_stage:
        try:
            field_defs = json.loads(cur_stage.custom_fields_json or "[]")
        except Exception:
            field_defs = []
        missing_required = []
        for fdef in field_defs:
            fid = fdef.get("id", "")
            if fdef.get("field_type") == "formula":
                continue
            val = str(submitted_cf.get(fid, "") or "").strip()
            if fdef.get("required") and not val and direction not in ("BACKWARD", "MANAGER_OVERRIDE"):
                missing_required.append(fdef.get("label", fid))
            if val:
                custom_fields_data[fid] = val
        if missing_required:
            raise HTTPException(400, f"Required column(s) not filled: {', '.join(missing_required)}")

        all_flow_stages = db.query(FMSStage).filter(FMSStage.flow_id == ticket.flow_id, FMSStage.is_deleted == False).all()
        ticket_tff = {}
        if ticket.ticket_custom_fields_json:
            try:
                ticket_tff = json.loads(ticket.ticket_custom_fields_json)
            except Exception:
                pass
        formula_lookup = {
            **ticket_tff,
            **_cross_stage_cf(db, ticket_id, all_flow_stages, split_id=_split_lineage_ids(db, ticket_id, split.id),
                               exclude_history_id=open_h.id if open_h else None),
            **custom_fields_data,
        }

        def _eval_formula(steps: list):
            result = None
            for i, step in enumerate(steps):
                raw = formula_lookup.get(step.get("col_id", ""), "")
                try:
                    val = float(raw)
                except (ValueError, TypeError):
                    return None
                if i == 0:
                    result = val
                    continue
                op = step.get("op", "+")
                if op == "+":
                    result += val
                elif op == "-":
                    result -= val
                elif op == "*":
                    result *= val
                elif op == "/":
                    if val == 0:
                        return None
                    result /= val
            if result is None:
                return None
            return str(int(result)) if result == int(result) else f"{result:.4f}".rstrip("0")

        for fdef in field_defs:
            if fdef.get("field_type") != "formula":
                continue
            computed = _eval_formula(fdef.get("formula_steps") or [])
            if computed is not None:
                custom_fields_data[fdef.get("id", "")] = computed

    if open_h:
        open_h.exited_at = datetime.utcnow()
        open_h.completion_note = completion_note.strip() or None
        open_h.evidence_url = evidence_url
        open_h.evidence_filename = evidence_filename
        open_h.custom_fields_data_json = json.dumps(custom_fields_data) if custom_fields_data else None
        _log(db, ticket_id, user.id, "STAGE_EXITED",
             f"Stage: {cur_stage.name if cur_stage else '?'} | Note: {completion_note[:80] or '—'} | "
             f"Evidence: {evidence_filename or '—'}")

    ticket.updated_at = datetime.utcnow()
    split.updated_at = datetime.utcnow()

    if terminal_complete:
        flow_for_rule = ticket.flow
        if flow_for_rule and flow_for_rule.closing_rule_json:
            try:
                rule = json.loads(flow_for_rule.closing_rule_json)
            except Exception:
                rule = None
            if rule and rule.get("col_id"):
                in_progress = {**formula_lookup, **custom_fields_data}
                stages_for_rule = db.query(FMSStage).filter(FMSStage.flow_id == ticket.flow_id, FMSStage.is_deleted == False).all()
                ok, err = _ticket_closing_rule_check(db, ticket, stages_for_rule, rule,
                                                      in_progress_split_id=split.id, in_progress_values=in_progress)
                if not ok:
                    raise HTTPException(400, err)
        split.status = "COMPLETED"
        _sync_ticket_cache(db, ticket)
        _mark_completed_by(ticket, user.id)
        _check_qty_discrepancy(db, ticket, user.id)
        _log(db, ticket_id, user.id, "COMPLETED", f"Completed terminal stage: {cur_stage.name if cur_stage else '?'}")
        db.commit()
        admins = _admin_ids(db, user.tenant_id)
        broadcast_sync(user.tenant_id, admins, FMS_STAGE_TRANSITION, {
            "ticket_id": ticket_id, "display_id": ticket.display_id, "title": ticket.title,
            "stage": cur_stage.name if cur_stage else "", "status": ticket.status,
        })
        return _build_ticket_detail_out(db, ticket)

    # Non-terminal branch — FMS Auto-Split Engine is opt-in (no-op unless the
    # current stage has split_enabled), safe to call with qty=0 here since
    # the mockup has no per-transition quantity field.
    if cur_stage is not None and direction == "FORWARD":
        split = _evaluate_auto_split(db, ticket, split, cur_stage, 0, custom_fields_data, formula_lookup,
                                      next_stage_id, new_assignee_id, user)

    new_h = FMSStageHistory(
        ticket_id=ticket_id, split_id=split.id, stage_id=next_stage_id, stage_name=next_stage.name,
        assignee_id=new_assignee_id, direction=direction, return_reason=return_reason.strip() or None,
        from_stage_id=cur_stage.id if cur_stage else None, from_stage_name=cur_stage.name if cur_stage else None,
    )
    db.add(new_h)
    split.current_stage_id = next_stage_id
    split.current_assignee_id = new_assignee_id

    if next_stage.is_terminal:
        flow_for_rule = ticket.flow
        if flow_for_rule and flow_for_rule.closing_rule_json:
            try:
                rule = json.loads(flow_for_rule.closing_rule_json)
            except Exception:
                rule = None
            if rule and rule.get("col_id"):
                in_progress = {**formula_lookup, **custom_fields_data}
                stages_for_rule = db.query(FMSStage).filter(FMSStage.flow_id == ticket.flow_id, FMSStage.is_deleted == False).all()
                ok, err = _ticket_closing_rule_check(db, ticket, stages_for_rule, rule,
                                                      in_progress_split_id=split.id, in_progress_values=in_progress)
                if not ok:
                    raise HTTPException(400, err)
        split.status = "COMPLETED"
        _log(db, ticket_id, user.id, "COMPLETED", f"Reached terminal stage: {next_stage.name}")
    else:
        split.status = "ACTIVE"

    _sync_ticket_cache(db, ticket)
    _mark_completed_by(ticket, user.id)
    _check_qty_discrepancy(db, ticket, user.id)

    event_type = "RETURNED" if direction == "BACKWARD" else ("MANAGER_OVERRIDE" if direction == "MANAGER_OVERRIDE" else "STAGE_ENTERED")
    new_assignee_obj = db.query(User).filter(User.id == new_assignee_id).first()
    detail_parts = [
        f"From: {cur_stage.name if cur_stage else '—'} → To: {next_stage.name}",
        f"Assignee: {new_assignee_obj.name if new_assignee_obj else '—'}",
    ]
    if return_reason:
        detail_parts.append(f"Reason: {return_reason}")
    _log(db, ticket_id, user.id, event_type, " | ".join(detail_parts))
    db.commit()

    admins = _admin_ids(db, user.tenant_id)
    managers = _manager_ids_for(db, new_assignee_id)
    notify_fms_stage_transition(db, user.tenant_id, ticket_id, ticket.title, next_stage.name, user.id, admins, managers, new_assignee_id,
                                 backward=(direction == "BACKWARD"))
    # WhatsApp excluded from both forward and backward stage transitions per
    # client rules — no send_whatsapp_for_fms_stage_transition call here.
    if next_stage.is_terminal:
        send_whatsapp_for_fms_ticket_closed(db, user.tenant_id, ticket, admins, managers, user.name)
    audience = list(set(admins + managers + [new_assignee_id]))
    broadcast_sync(user.tenant_id, audience, FMS_STAGE_TRANSITION, {
        "ticket_id": ticket_id, "display_id": ticket.display_id, "title": ticket.title,
        "stage": next_stage.name, "status": ticket.status,
    })
    if direction == "BACKWARD":
        # Also notify the assignee whose ticket just got sent back, not just
        # manager/admin (previously excluded — a real gap, now fixed).
        for mid in set(_manager_ids_for(db, new_assignee_id) + admins + [new_assignee_id]):
            if not mid:
                continue
            create_notification(
                db, user.tenant_id, user_id=mid, notif_type="FMS_BACKWARD_MOVE",
                title=f"Ticket returned: {ticket.title}",
                body=f"{user.name} returned {ticket.display_id or ticket_id} to {next_stage.name}. Reason: {return_reason[:200]}",
                link=f"/fms/dashboard?view=stage&flow_id={ticket.flow_id}&stage_id={next_stage_id}",
            )

    return _build_ticket_detail_out(db, ticket)


class TicketActionIn(BaseModel):
    action: str
    comment: str = ""
    reason: str = ""
    new_assignee_id: str = ""
    helper_id: str = ""
    flag_reason: str = ""


@router.post("/tickets/{ticket_id}/action", response_model=FMSTicketDetailOut)
def ticket_action(ticket_id: str, body: TicketActionIn, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    from ..fms import _admin_ids, _can_act_on_ticket, _get_ticket, _log, _manager_ids_for, _open_history
    from ..notifications import notify_fms_flagged, send_whatsapp_for_fms_ticket_closed

    from ..fms import _resolve_linked_flow, _spawn_linked_ticket, _notify_linked_parent_if_ready

    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    action = body.action
    if action != "add_helper" and not _can_act_on_ticket(user, ticket, None):
        raise HTTPException(403, "Only the assigned employee for this stage can act on this ticket")

    if action == "comment" and body.comment.strip():
        _log(db, ticket_id, user.id, "COMMENT", body.comment.strip())

    elif action == "reassign" and body.new_assignee_id and body.reason.strip():
        if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
            raise HTTPException(403, "Only the current assignee can reassign")
        old_assignee = ticket.current_assignee_id
        ticket.current_assignee_id = body.new_assignee_id
        open_h = _open_history(db, ticket_id)
        if open_h:
            open_h.assignee_id = body.new_assignee_id
        _log(db, ticket_id, user.id, "REASSIGNED", f"From: {old_assignee} → To: {body.new_assignee_id} | {body.reason}")

    elif action == "add_helper" and body.helper_id:
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        existing = db.query(FMSTicketHelper).filter(
            FMSTicketHelper.ticket_id == ticket_id, FMSTicketHelper.user_id == body.helper_id).first()
        if not existing:
            db.add(FMSTicketHelper(ticket_id=ticket_id, user_id=body.helper_id, added_by_id=user.id, reason=body.reason.strip() or None))
            _log(db, ticket_id, user.id, "HELPER_ADDED", f"Helper: {body.helper_id} | {body.reason}")

    elif action == "flag" and body.flag_reason.strip():
        if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
            raise HTTPException(403, "Only the current assignee can flag this ticket")
        ticket.is_flagged = True
        ticket.flagged_reason = body.flag_reason.strip()
        _log(db, ticket_id, user.id, "FLAGGED", body.flag_reason.strip())
        admins = _admin_ids(db, user.tenant_id)
        managers = _manager_ids_for(db, ticket.current_assignee_id)
        notify_fms_flagged(db, user.tenant_id, ticket, admins, managers, body.flag_reason.strip(), user.name, actor_id=user.id)

    elif action == "unflag":
        if user.role == "EMPLOYEE" and ticket.current_assignee_id != user.id:
            raise HTTPException(403, "Only the current assignee can unflag this ticket")
        ticket.is_flagged = False
        ticket.flagged_reason = None
        _log(db, ticket_id, user.id, "UNFLAGGED")

    elif action == "on_hold":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        ticket.status = "ON_HOLD"
        _log(db, ticket_id, user.id, "ON_HOLD", body.reason)

    elif action == "resume":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        ticket.status = "ACTIVE"  # matches desktop — no snapshot of pre-hold status
        ticket.pause_reason = None
        ticket.linked_child_ticket_id = None
        ticket.is_flagged = False
        ticket.flagged_reason = None
        _log(db, ticket_id, user.id, "RESUMED")

    elif action == "close":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        ticket.status = "CLOSED"
        ticket.closed_at = datetime.utcnow()
        _log(db, ticket_id, user.id, "CLOSED", body.reason)
        _notify_linked_parent_if_ready(db, ticket)
        admins = _admin_ids(db, user.tenant_id)
        managers = _manager_ids_for(db, ticket.current_assignee_id)
        send_whatsapp_for_fms_ticket_closed(db, user.tenant_id, ticket, admins, managers, user.name)

    elif action == "send_to_linked_flow":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        stage = ticket.current_stage
        target_flow = _resolve_linked_flow(db, user.tenant_id, stage.linked_library_flow_id if stage else None)
        if not target_flow:
            raise HTTPException(400, "This stage has no linked flow configured (or it isn't deployed to this tenant)")
        linked = _spawn_linked_ticket(db, ticket, target_flow, user,
                                       title=f"{ticket.title} — linked from {ticket.display_id}")
        linked.linked_parent_ticket_id = ticket.id
        ticket.status = "ON_HOLD"
        ticket.linked_child_ticket_id = linked.id
        ticket.pause_reason = f"Waiting on {linked.display_id} ({target_flow.name})"
        _log(db, ticket_id, user.id, "SENT_TO_LINKED_FLOW",
             f"Spawned {linked.display_id} on '{target_flow.name}'" + (f" | Reason: {body.reason.strip()}" if body.reason.strip() else ""))

    elif action == "close_and_continue":
        if user.role not in ("ADMIN", "MANAGER"):
            raise HTTPException(403, "Managers only")
        target_flow = _resolve_linked_flow(db, user.tenant_id, ticket.flow.next_library_flow_id)
        if not target_flow:
            raise HTTPException(400, "This flow has no continuation flow configured (or it isn't deployed to this tenant)")
        ticket.status = "CLOSED"
        ticket.closed_at = datetime.utcnow()
        _log(db, ticket_id, user.id, "CLOSED", body.reason)
        continuation = _spawn_linked_ticket(db, ticket, target_flow, user)
        continuation.continued_from_ticket_id = ticket.id
        ticket.continued_to_ticket_id = continuation.id
        _log(db, ticket_id, user.id, "CONTINUED", f"Continued as {continuation.display_id} on '{target_flow.name}'")
        admins = _admin_ids(db, user.tenant_id)
        managers = _manager_ids_for(db, ticket.current_assignee_id)
        send_whatsapp_for_fms_ticket_closed(db, user.tenant_id, ticket, admins, managers, user.name)

    else:
        raise HTTPException(400, "Invalid or incomplete action")

    ticket.updated_at = datetime.utcnow()
    db.commit()
    return _build_ticket_detail_out(db, ticket)


class HelpRequestIn(BaseModel):
    reason: str
    helper_id: Optional[str] = None


@router.post("/tickets/{ticket_id}/help_request", response_model=FMSTicketDetailOut)
def help_request(ticket_id: str, body: HelpRequestIn, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    from ..fms import _admin_ids, _get_ticket, _log, _manager_ids_for
    from ..notifications import create_notification
    from ..notification_rules import channel_enabled, filter_recipients

    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    if not body.reason.strip():
        raise HTTPException(400, "Reason is required")
    ticket.status = "HELP_REQUESTED"
    _log(db, ticket_id, user.id, "HELP_REQUESTED", body.reason.strip())

    admins = _admin_ids(db, user.tenant_id)
    mgrs = _manager_ids_for(db, ticket.current_assignee_id)
    recipients = filter_recipients(
        db, user.tenant_id, "fms_help_needed",
        admin_ids=admins, manager_ids=mgrs,
        assignee_id=ticket.current_assignee_id, actor_id=user.id,
    )
    for uid in recipients:
        create_notification(
            db, user.tenant_id, user_id=uid, notif_type="FMS_HELP_NEEDED",
            title=f"Help needed: {ticket.title}",
            body=f"{user.name} needs help on {ticket.display_id or ticket_id}. Reason: {body.reason[:200]}",
            link=f"/fms/dashboard?view=stage&flow_id={ticket.flow_id}&stage_id={ticket.current_stage_id}",
            condition_key="fms_help_needed",
        )
    if channel_enabled(db, user.tenant_id, "fms_help_needed", "whatsapp"):
        from ..database import User as _User
        try:
            for uid in recipients:
                recipient = db.query(_User).filter(_User.id == uid).first()
                if not recipient or not recipient.phone:
                    continue
                from ..notifications import _send_gupshup_wa
                variables = [recipient.name, ticket.title, user.name, body.reason[:200]]
                _send_gupshup_wa(db, user.tenant_id, recipient, "omniflow_fms_help_needed", variables,
                                  related_entity_type="fms_ticket", related_entity_id=ticket_id,
                                  event_key="fms_help_needed")
        except Exception:
            pass
    if body.helper_id:
        existing = db.query(FMSTicketHelper).filter(
            FMSTicketHelper.ticket_id == ticket_id, FMSTicketHelper.user_id == body.helper_id).first()
        if not existing:
            db.add(FMSTicketHelper(ticket_id=ticket_id, user_id=body.helper_id, added_by_id=user.id, reason=body.reason.strip()))

    ticket.updated_at = datetime.utcnow()
    db.commit()
    return _build_ticket_detail_out(db, ticket)


class BulkTransitionIn(BaseModel):
    ticket_ids: List[str]
    next_stage_id: str


class BulkTransitionOut(BaseModel):
    moved: int
    skipped: List[dict]


@router.post("/tickets/bulk-transition", response_model=BulkTransitionOut)
def bulk_transition(body: BulkTransitionIn, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Mirrors app/fms.py's fms_bulk_transition exact simplifications: max 20
    tickets, FORWARD-only (no override/backward), assignee always the next
    stage's configured default, evidence_required NOT enforced (a real
    desktop gap, replicated intentionally, not fixed)."""
    from ..fms import (
        _admin_ids,
        _can_transition,
        _check_qty_discrepancy,
        _ensure_ticket_has_split,
        _get_ticket,
        _log,
        _manager_ids_for,
        _mark_completed_by,
        _open_history,
        _stage_default_assignee,
        _sync_ticket_cache,
        _notify_linked_parent_if_ready,
    )
    from ..notifications import notify_fms_stage_transition

    if len(body.ticket_ids) > 20:
        raise HTTPException(400, "Max 20 tickets per bulk transition")
    next_stage = db.query(FMSStage).filter(FMSStage.id == body.next_stage_id).first()
    if not next_stage:
        raise HTTPException(400, "Invalid next stage")

    moved = 0
    skipped: List[dict] = []
    for tid_ in body.ticket_ids:
        try:
            ticket = _get_ticket(db, tid_, user.tenant_id)
        except HTTPException:
            skipped.append({"ticket_id": tid_, "reason": "Not found"})
            continue
        if ticket.flow_id != next_stage.flow_id:
            skipped.append({"ticket_id": tid_, "reason": "Different flow"})
            continue
        split = _ensure_ticket_has_split(db, ticket)
        if not _can_transition(user, ticket, split):
            skipped.append({"ticket_id": tid_, "reason": "Not authorised"})
            continue
        if ticket.status == "CLOSED" or split.status in ("CLOSED", "COMPLETED"):
            skipped.append({"ticket_id": tid_, "reason": "Closed or completed"})
            continue
        cur_stage = split.current_stage
        if not cur_stage or next_stage.order != cur_stage.order + 1:
            skipped.append({"ticket_id": tid_, "reason": "Not adjacent to current stage"})
            continue
        if cur_stage.completion_note_required:
            skipped.append({"ticket_id": tid_, "reason": f"Stage '{cur_stage.name}' requires a completion note — use single transition"})
            continue
        try:
            field_defs = json.loads(cur_stage.custom_fields_json or "[]")
        except Exception:
            field_defs = []
        if any(f.get("required") and f.get("field_type") != "formula" for f in field_defs):
            skipped.append({"ticket_id": tid_, "reason": f"Stage '{cur_stage.name}' has required fields — use single transition"})
            continue

        new_assignee_id = _stage_default_assignee(next_stage) or split.current_assignee_id
        open_h = _open_history(db, tid_, split_id=split.id)
        if open_h:
            open_h.exited_at = datetime.utcnow()
        db.add(FMSStageHistory(
            ticket_id=tid_, split_id=split.id, stage_id=next_stage.id, stage_name=next_stage.name,
            assignee_id=new_assignee_id, direction="FORWARD",
            from_stage_id=cur_stage.id, from_stage_name=cur_stage.name,
        ))
        split.current_stage_id = next_stage.id
        split.current_assignee_id = new_assignee_id
        split.status = "COMPLETED" if next_stage.is_terminal else "ACTIVE"
        split.updated_at = datetime.utcnow()
        _sync_ticket_cache(db, ticket)
        _mark_completed_by(ticket, user.id)
        _check_qty_discrepancy(db, ticket, user.id)
        _notify_linked_parent_if_ready(db, ticket)
        _log(db, tid_, user.id, "COMPLETED" if next_stage.is_terminal else "STAGE_ENTERED",
             f"Bulk move: {cur_stage.name} → {next_stage.name}")
        ticket.updated_at = datetime.utcnow()
        admins = _admin_ids(db, user.tenant_id)
        managers = _manager_ids_for(db, new_assignee_id)
        notify_fms_stage_transition(db, user.tenant_id, tid_, ticket.title, next_stage.name, user.id, admins, managers, new_assignee_id)
        moved += 1

    db.commit()
    return BulkTransitionOut(moved=moved, skipped=skipped)
