"""
In-app notification helpers — Phase 0-D
Phase 1 addition: each helper also fires a WebSocket broadcast so connected
clients get the event in real-time without polling.
"""
from .database import Notification
from .ws_manager import (
    broadcast_sync,
    TICKET_ASSIGNED, TICKET_COMMENTED, TICKET_FLAGGED,
    TICKET_HELP_REQUESTED, TICKET_STATUS_CHANGED,
    CHECKLIST_DUE_SOON, CHECKLIST_OVERDUE, CHECKLIST_COMPLETED,
    NOTIFICATION_NEW,
)


def create_notification(db, tenant_id: str, user_id: str,
                         notif_type: str, title: str,
                         body: str = "", link: str = ""):
    """
    Add a notification record and fire a NOTIFICATION_NEW WS event.
    Caller must commit the DB session after calling this.
    """
    db.add(Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        notif_type=notif_type,
        title=title,
        body=body,
        link=link,
    ))
    # Real-time push — audience: specific user (1-6 NOTIFICATION_NEW)
    broadcast_sync(tenant_id, [user_id], NOTIFICATION_NEW, {
        "notif_type": notif_type,
        "title": title,
        "body": body,
        "link": link,
    })


def notify_ticket_assigned(db, ticket, assignee):
    """Phase 0-D-2  |  1-6: TICKET_ASSIGNED — audience: assignee"""
    due_str = ticket.due_at.strftime("%d %b") if ticket.due_at else "N/A"
    create_notification(
        db, ticket.tenant_id, assignee.id,
        "TICKET_ASSIGNED",
        f"New ticket: {ticket.title}",
        f"Priority: {ticket.priority} · Due: {due_str}",
        f"/tickets/{ticket.id}",
    )
    # Additional direct TICKET_ASSIGNED broadcast (separate from notification bubble)
    broadcast_sync(ticket.tenant_id, [assignee.id], TICKET_ASSIGNED, {
        "ticket_id":   ticket.id,
        "ticket_title": ticket.title,
        "priority":    ticket.priority,
        "link":        f"/tickets/{ticket.id}",
    })


def notify_ticket_reminder(db, ticket, assignee):
    """P5-06: TICKET_REMINDER — audience: assignee only."""
    due_str = ticket.due_at.strftime("%d %b") if ticket.due_at else "N/A"
    create_notification(
        db, ticket.tenant_id, assignee.id,
        "TICKET_REMINDER",
        f"Reminder: {ticket.title}",
        f"Priority: {ticket.priority} · Due: {due_str}",
        f"/tickets/{ticket.id}",
    )
    broadcast_sync(ticket.tenant_id, [assignee.id], TICKET_ASSIGNED, {
        "ticket_id":    ticket.id,
        "ticket_title": ticket.title,
        "priority":     ticket.priority,
        "link":         f"/tickets/{ticket.id}",
        "reminder":     True,
    })


def notify_helper_added(db, ticket, helper):
    """Phase 0-C-1/2  |  1-6: TICKET_ASSIGNED variant for helpers"""
    create_notification(
        db, ticket.tenant_id, helper.id,
        "TICKET_HELPER",
        f"Added as helper: {ticket.title}",
        link=f"/tickets/{ticket.id}",
    )
    broadcast_sync(ticket.tenant_id, [helper.id], TICKET_ASSIGNED, {
        "ticket_id":    ticket.id,
        "ticket_title": ticket.title,
        "role":         "helper",
        "link":         f"/tickets/{ticket.id}",
    })


def notify_ticket_status_changed(db, ticket, actor_id: str,
                                  old_status: str, new_status: str,
                                  admin_ids: list, manager_ids: list):
    """
    1-6: TICKET_STATUS_CHANGED
    Audience: admin + scoped managers + assignee.
    """
    audience = list(set(admin_ids + manager_ids + [ticket.current_assignee_id or ""]))
    audience = [uid for uid in audience if uid]
    broadcast_sync(ticket.tenant_id, audience, TICKET_STATUS_CHANGED, {
        "ticket_id":    ticket.id,
        "ticket_title": ticket.title,
        "old_status":   old_status,
        "new_status":   new_status,
        "actor_id":     actor_id,
        "link":         f"/tickets/{ticket.id}",
    })


def notify_ticket_commented(db, ticket, commenter_id: str, helper_ids: list):
    """
    1-6: TICKET_COMMENTED
    Audience: assignee + helpers + creator.
    """
    audience = list(set(
        [ticket.current_assignee_id or "", ticket.created_by_id or ""]
        + helper_ids
    ))
    audience = [uid for uid in audience if uid and uid != commenter_id]
    broadcast_sync(ticket.tenant_id, audience, TICKET_COMMENTED, {
        "ticket_id":    ticket.id,
        "ticket_title": ticket.title,
        "commenter_id": commenter_id,
        "link":         f"/tickets/{ticket.id}",
    })


def notify_ticket_flagged(db, ticket, actor_id: str, admin_ids: list):
    """
    1-6: TICKET_FLAGGED
    Audience: admin + assignee.
    """
    audience = list(set(admin_ids + [ticket.current_assignee_id or ""]))
    audience = [uid for uid in audience if uid]
    broadcast_sync(ticket.tenant_id, audience, TICKET_FLAGGED, {
        "ticket_id":     ticket.id,
        "ticket_title":  ticket.title,
        "flagged_reason": ticket.flagged_reason or "",
        "link":          f"/tickets/{ticket.id}",
    })


def notify_ticket_help_requested(db, ticket, actor_id: str,
                                  admin_ids: list, manager_ids: list):
    """
    1-6: TICKET_HELP_REQUESTED
    Audience: admin + scoped managers.
    """
    audience = list(set(admin_ids + manager_ids))
    broadcast_sync(ticket.tenant_id, audience, TICKET_HELP_REQUESTED, {
        "ticket_id":    ticket.id,
        "ticket_title": ticket.title,
        "actor_id":     actor_id,
        "link":         f"/tickets/{ticket.id}",
    })


def notify_checklist_due(db, assignment):
    """Phase 0-D-3  |  1-6: CHECKLIST_DUE_SOON — audience: assigned user"""
    title = assignment.template.title if assignment.template else "Checklist"
    due_str = assignment.due_at.strftime("%d %b %I:%M %p") if assignment.due_at else ""
    create_notification(
        db, assignment.tenant_id, assignment.user_id,
        "CHECKLIST_DUE",
        f"Checklist due: {title}",
        f"Due at {due_str}",
        "/checklists",
    )
    broadcast_sync(assignment.tenant_id, [assignment.user_id], CHECKLIST_DUE_SOON, {
        "assignment_id": assignment.id,
        "title":         title,
        "due_at":        due_str,
        "link":          "/checklists",
    })


def notify_checklist_overdue(db, assignment, admin_ids: list, manager_ids: list):
    """
    1-6: CHECKLIST_OVERDUE
    Audience: admin + managers + assigned user.
    """
    title = assignment.template.title if assignment.template else "Checklist"
    audience = list(set(admin_ids + manager_ids + [assignment.user_id]))
    broadcast_sync(assignment.tenant_id, audience, CHECKLIST_OVERDUE, {
        "assignment_id": assignment.id,
        "title":         title,
        "user_id":       assignment.user_id,
        "link":          "/checklists",
    })


def notify_checklist_completed(db, assignment, admin_ids: list, manager_ids: list):
    """
    1-6: CHECKLIST_COMPLETED
    Audience: admin + managers.
    """
    title = assignment.template.title if assignment.template else "Checklist"
    audience = list(set(admin_ids + manager_ids))
    broadcast_sync(assignment.tenant_id, audience, CHECKLIST_COMPLETED, {
        "assignment_id": assignment.id,
        "title":         title,
        "user_id":       assignment.user_id,
        "link":          "/checklists",
    })


# ── Phase 2/4 stubs — routing logic defined now (per §18.2 plan) ─────────────

def notify_fms_stage_transition(tenant_id: str, ticket_id: str, ticket_title: str,
                                 new_stage: str, actor_id: str,
                                 admin_ids: list, manager_ids: list, new_assignee_id: str):
    """
    1-6: FMS_STAGE_TRANSITION (used in Phase 2)
    Audience: admin + scoped managers + new assignee.
    """
    from .ws_manager import FMS_STAGE_TRANSITION
    audience = list(set(admin_ids + manager_ids + [new_assignee_id or ""]))
    audience = [uid for uid in audience if uid]
    broadcast_sync(tenant_id, audience, FMS_STAGE_TRANSITION, {
        "ticket_id":    ticket_id,
        "ticket_title": ticket_title,
        "new_stage":    new_stage,
        "actor_id":     actor_id,
    })


def notify_store_alert(tenant_id: str, alert_type: str, message: str,
                        store_manager_ids: list):
    """
    1-6: STORE_ALERT (used in Phase 4)
    Audience: Store Manager role only.
    """
    from .ws_manager import STORE_ALERT
    broadcast_sync(tenant_id, store_manager_ids, STORE_ALERT, {
        "alert_type": alert_type,
        "message":    message,
    })
