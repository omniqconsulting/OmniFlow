"""
My Tasks — single consolidated action queue for the logged-in user, pulling
together everything they personally need to work on across Tickets,
Delegations, FMS, Checklists and (for sales roles) the customer follow-up
queue, plus a CSV export of what they've completed. Complements — does not
replace — the individual module tabs, which remain for full CRUD/detail work.
"""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from .database import (
    get_db, Tenant, User,
    Ticket, TicketAssignee,
    ChecklistAssignment,
    FMSTicket, FMSStageHistory, FMSTicketHelper,
    CRMCallLog,
)
from .auth import get_current_user_or_redirect, get_nav_flags
from .labels import get_labels, DEFAULT_L
from .templates_env import templates

router = APIRouter()


def _L(db, user):
    if user is None:
        return DEFAULT_L
    return get_labels(db, user.tenant_id)


def _unread(db: Session, user: User) -> int:
    from .database import Notification
    return db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read == False).count()


def _ctx(request, user, db, **kw):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first() if user else None
    return {
        "request": request, "user": user,
        "L": _L(db, user), "unread": _unread(db, user),
        **get_nav_flags(db, user, tenant),
        **kw,
    }


TICKET_OPEN_STATUSES = ("DONE", "CLOSED")
FMS_OPEN_STATUSES = ("COMPLETED", "CLOSED")
CHECKLIST_OPEN_STATUSES = ("PENDING", "IN_PROGRESS", "OVERDUE")

# Per-kind visual identity + labels, shared by desktop/mobile templates so
# each module reads as its own color-coded lane inside the unified queue.
KIND_META = {
    "delegation":     {"label": "Delegation",  "color": "#3b82f6", "open_label": "Open Delegation"},
    "ticket":         {"label": "Ticket",       "color": "#8b5cf6", "open_label": "Open Ticket"},
    "fms":            {"label": "Flow Board",  "color": "#f59e0b", "open_label": "Open Flow Board"},
    "checklist":      {"label": "Checklist",   "color": "#10b981", "open_label": "Open Checklists"},
    "sales_followup": {"label": "Follow-up",   "color": "#ec4899", "open_label": "Log Call"},
}


def get_my_task_items(db: Session, user: User, tid: str) -> dict:
    """Aggregate every open action item the given user needs to work on,
    across delegations, tickets, FMS and checklists (plus sales follow-ups
    when the tenant has SALES enabled). Shared by the employee dashboard and
    the My Tasks page so both stay in sync."""
    tenant = db.query(Tenant).filter(Tenant.id == tid).first()

    helper_ticket_ids = [
        h.ticket_id for h in db.query(TicketAssignee).filter(
            TicketAssignee.user_id == user.id).all()
    ]
    all_my_tickets = db.query(Ticket).filter(
        Ticket.tenant_id == tid,
        Ticket.is_deleted == False,
        (
            (Ticket.current_assignee_id == user.id) |
            (Ticket.created_by_id == user.id) |
            (Ticket.id.in_(helper_ticket_ids))
        ),
    ).order_by(Ticket.created_at.desc()).all()

    delegations = [t for t in all_my_tickets if t.ticket_type == "D"]
    tickets = [t for t in all_my_tickets if t.ticket_type != "D"]

    open_delegations = [t for t in delegations if t.status not in TICKET_OPEN_STATUSES]
    open_tickets = [t for t in tickets if t.status not in TICKET_OPEN_STATUSES]
    done_delegations = [t for t in delegations if t.status in TICKET_OPEN_STATUSES]
    done_tickets = [t for t in tickets if t.status in TICKET_OPEN_STATUSES]

    fms_hist_tids = [
        h.ticket_id for h in db.query(FMSStageHistory).filter(
            FMSStageHistory.assignee_id == user.id).all()
    ]
    fms_helper_tids = [
        h.ticket_id for h in db.query(FMSTicketHelper).filter(
            FMSTicketHelper.user_id == user.id).all()
    ]
    all_fms_ids = set(fms_hist_tids) | set(fms_helper_tids)
    my_fms_tickets = db.query(FMSTicket).filter(
        FMSTicket.tenant_id == tid,
        FMSTicket.is_deleted == False,
        (
            (FMSTicket.current_assignee_id == user.id) |
            (FMSTicket.id.in_(all_fms_ids))
        ),
    ).order_by(FMSTicket.updated_at.desc()).all()

    open_fms = [t for t in my_fms_tickets if t.status not in FMS_OPEN_STATUSES]
    done_fms = [t for t in my_fms_tickets if t.status in FMS_OPEN_STATUSES]

    my_checklists = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == tid,
        ChecklistAssignment.user_id == user.id,
        ChecklistAssignment.is_deleted == False,
    ).order_by(ChecklistAssignment.due_at).all()
    open_checklists = [a for a in my_checklists if a.status in CHECKLIST_OPEN_STATUSES]
    done_checklists = [a for a in my_checklists if a.status not in CHECKLIST_OPEN_STATUSES]

    has_sales = get_nav_flags(db, user, tenant)["has_sales"]
    open_followups, done_followups = [], []
    if has_sales:
        my_followups = db.query(CRMCallLog).filter(
            CRMCallLog.tenant_id == tid,
            CRMCallLog.agent_id == user.id,
            CRMCallLog.follow_up_at != None,
        ).order_by(CRMCallLog.follow_up_at).all()
        open_followups = [c for c in my_followups if not c.follow_up_done]
        done_followups = [c for c in my_followups if c.follow_up_done]

    def _item(kind, obj, id_, title, status, priority, due_at, url, quick_action=None):
        meta = KIND_META[kind]
        return {"kind": kind, "id": id_, "title": title, "status": status,
                "priority": priority, "due_at": due_at, "url": url, "obj": obj,
                "color": meta["color"], "kind_label": meta["label"],
                "open_label": meta["open_label"], "action": quick_action}

    action_queue = []
    for t in open_delegations:
        qa = None if t.acknowledged_at else {"label": "Acknowledge", "url": f"/tickets/{t.id}/acknowledge", "method": "post"}
        action_queue.append(_item("delegation", t, t.id, t.title, t.status, t.priority,
                                   t.due_at, f"/tickets/{t.id}", qa))
    for t in open_tickets:
        qa = None if t.acknowledged_at else {"label": "Acknowledge", "url": f"/tickets/{t.id}/acknowledge", "method": "post"}
        action_queue.append(_item("ticket", t, t.id, t.title, t.status, t.priority,
                                   t.due_at, f"/tickets/{t.id}", qa))
    for t in open_fms:
        action_queue.append(_item("fms", t, t.id, t.title, t.status, t.priority,
                                   t.due_at, f"/fms/tickets/{t.id}"))
    for a in open_checklists:
        title = a.template.title if a.template else "Checklist"
        qa = {"label": "Start", "url": f"/checklists/start/{a.id}", "method": "post"} if a.status == "PENDING" else None
        action_queue.append(_item("checklist", a, a.id, title, a.status, None,
                                   a.due_at, "/checklists", qa))
    for c in open_followups:
        title = c.customer.name if c.customer else "Follow-up"
        qa = {"label": "Log Call", "url": f"/sales/contacts/{c.customer_id}", "method": "get"}
        action_queue.append(_item("sales_followup", c, c.id, title, "PENDING", None,
                                   c.follow_up_at, f"/sales/contacts/{c.customer_id}", qa))

    action_queue.sort(key=lambda i: i["due_at"] or datetime.max)

    return {
        "action_queue": action_queue,
        "delegations": open_delegations, "delegations_done": done_delegations,
        "tickets": open_tickets, "tickets_done": done_tickets,
        "fms": open_fms, "fms_done": done_fms,
        "checklists": open_checklists, "checklists_done": done_checklists,
        "sales_followups": open_followups, "sales_followups_done": done_followups,
        "has_sales": has_sales,
        "kind_meta": KIND_META,
        "counts": {
            "delegations": len(open_delegations),
            "tickets": len(open_tickets),
            "fms": len(open_fms),
            "checklists": len(open_checklists),
            "sales_followups": len(open_followups),
        },
    }


def _due_bucket(item, now):
    due = item["due_at"]
    if not due:
        return "none"
    if due < now.replace(hour=0, minute=0, second=0, microsecond=0):
        return "overdue"
    if due.date() == now.date():
        return "today"
    return "upcoming"


@router.get("/my-tasks", response_class=HTMLResponse)
def my_tasks(request: Request,
             type: str = "",
             priority: str = "",
             due: str = "",
             user: User = Depends(get_current_user_or_redirect),
             db: Session = Depends(get_db)):
    if type not in KIND_META:
        type = ""

    tid = user.tenant_id
    data = get_my_task_items(db, user, tid)
    now = datetime.utcnow()

    queue = data["action_queue"]
    if type:
        queue = [i for i in queue if i["kind"] == type]
    if priority:
        queue = [i for i in queue if i["priority"] == priority]

    due_counts = {"overdue": 0, "today": 0, "upcoming": 0}
    for i in queue:
        b = _due_bucket(i, now)
        if b in due_counts:
            due_counts[b] += 1
    data["due_counts"] = due_counts

    if due:
        queue = [i for i in queue if _due_bucket(i, now) == due]
    data["action_queue"] = queue

    data["total_pending"] = sum(data["counts"].values())
    data["modules_count"] = sum(1 for v in data["counts"].values() if v > 0)
    data["kind_counts"] = {
        "delegation": data["counts"]["delegations"],
        "ticket": data["counts"]["tickets"],
        "fms": data["counts"]["fms"],
        "checklist": data["counts"]["checklists"],
        "sales_followup": data["counts"]["sales_followups"],
    }

    # Group filtered items by kind so each module renders as its own
    # color-coded section (see KIND_META) instead of one flat table.
    grouped = {k: [] for k in KIND_META}
    for i in queue:
        grouped[i["kind"]].append(i)
    data["grouped_queue"] = grouped

    template_name = "my_tasks_mobile.html" if request.cookies.get("pwa_ui") == "1" else "my_tasks.html"
    return templates.TemplateResponse(request, template_name, _ctx(
        request, user, db,
        type=type, priority=priority, due=due, now=now, **data,
    ))


@router.get("/my-tasks/export")
def export_my_tasks(export_type: str = "all",
                     user: User = Depends(get_current_user_or_redirect),
                     db: Session = Depends(get_db)):
    """CSV export of the current user's own completed work — no manager gate,
    since it only ever covers the requesting user's own tasks."""
    tid = user.tenant_id
    data = get_my_task_items(db, user, tid)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Type", "Title", "Status", "Priority", "Completed/Closed"])

    def _rows(kind, items, title_fn, completed_fn):
        for item in items:
            w.writerow([kind, title_fn(item), item.status, getattr(item, "priority", ""),
                        completed_fn(item)])

    if export_type in ("all", "delegations"):
        _rows("Delegation", data["delegations_done"], lambda t: t.title,
              lambda t: t.closed_at.strftime("%Y-%m-%d") if t.closed_at else "")
    if export_type in ("all", "tickets"):
        _rows("Ticket", data["tickets_done"], lambda t: t.title,
              lambda t: t.closed_at.strftime("%Y-%m-%d") if t.closed_at else "")
    if export_type in ("all", "fms"):
        _rows("FMS", data["fms_done"], lambda t: t.title,
              lambda t: t.completed_at.strftime("%Y-%m-%d") if t.completed_at else "")
    if export_type in ("all", "checklists"):
        for a in data["checklists_done"]:
            w.writerow(["Checklist", a.template.title if a.template else "", a.status, "",
                        a.completed_at.strftime("%Y-%m-%d") if a.completed_at else ""])
    if export_type in ("all", "sales") and data["has_sales"]:
        for c in data["sales_followups_done"]:
            w.writerow(["Sales follow-up", c.customer.name if c.customer else "", "DONE", "",
                        c.contacted_at.strftime("%Y-%m-%d") if c.contacted_at else ""])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=my_tasks_export.csv"},
    )
