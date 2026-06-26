"""
In-app notification helpers — Phase 0-D
Phase 1 addition: each helper also fires a WebSocket broadcast so connected
clients get the event in real-time without polling.
"""
import json, logging
from datetime import datetime, timedelta
logger = logging.getLogger("notifications")


# ── E-15: Office Hours Helpers ────────────────────────────────────────────────

def is_within_office_hours(tenant, dt=None):
    """Return True if dt (defaults to now) falls within the tenant's configured working hours."""
    if not getattr(tenant, 'work_start_time', None):
        return True  # Not configured — send anytime
    try:
        import pytz
        tz = pytz.timezone(tenant.timezone or 'Asia/Kolkata')
    except Exception:
        return True
    now = dt or datetime.utcnow().replace(tzinfo=pytz.utc)
    if now.tzinfo is None:
        now = pytz.utc.localize(now)
    local = now.astimezone(tz)
    work_days = [int(d) for d in (tenant.work_days or '0,1,2,3,4').split(',') if d.strip()]
    if local.weekday() not in work_days:
        return False
    start_h, start_m = map(int, tenant.work_start_time.split(':'))
    end_h, end_m = map(int, tenant.work_end_time.split(':'))
    local_minutes = local.hour * 60 + local.minute
    return (start_h * 60 + start_m) <= local_minutes < (end_h * 60 + end_m)


def business_hours_elapsed(tenant, start_dt, end_dt):
    """Return elapsed business hours between two datetimes for the tenant's office hours config."""
    if not getattr(tenant, 'work_start_time', None):
        delta = (end_dt - start_dt).total_seconds() / 3600
        return max(0, delta)
    try:
        import pytz
        tz = pytz.timezone(tenant.timezone or 'Asia/Kolkata')
    except Exception:
        return max(0, (end_dt - start_dt).total_seconds() / 3600)

    work_days = [int(d) for d in (tenant.work_days or '0,1,2,3,4').split(',') if d.strip()]
    start_h, start_m = map(int, tenant.work_start_time.split(':'))
    end_h, end_m = map(int, tenant.work_end_time.split(':'))
    day_minutes = end_h * 60 + end_m - start_h * 60 - start_m

    if start_dt.tzinfo is None:
        import pytz as _pytz
        start_dt = _pytz.utc.localize(start_dt)
    if end_dt.tzinfo is None:
        import pytz as _pytz
        end_dt = _pytz.utc.localize(end_dt)

    elapsed = 0.0
    cursor = start_dt.astimezone(tz)
    end_local = end_dt.astimezone(tz)

    while cursor < end_local:
        if cursor.weekday() in work_days:
            day_start = cursor.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            day_end = cursor.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
            period_start = max(cursor, day_start)
            period_end = min(end_local, day_end)
            if period_end > period_start:
                elapsed += (period_end - period_start).total_seconds() / 3600
        # Advance to next calendar day at work_start_time
        next_day = (cursor + timedelta(days=1)).replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        cursor = next_day

    return elapsed

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


def send_whatsapp_for_ticket_assigned(db, ticket, assignee):
    """
    WhatsApp send for ticket_assigned — Pipeline 1.
    Never allowed to raise back into the caller.
    Always logs an attempt row regardless of outcome.
    """
    from .database import WhatsAppMessageLog
    from .services.msg91 import send_whatsapp_template, format_wa_date

    due_str = format_wa_date(ticket.due_at) if ticket.due_at else "N/A"
    variables = [assignee.name, ticket.title, ticket.priority, due_str]

    try:
        if not assignee.mobile_verified:
            status, error = "SKIPPED_UNVERIFIED", None
        else:
            success, error = send_whatsapp_template(
                assignee.phone, "omniflow_ticket_assigned", variables)
            status = "SENT" if success else "FAILED"

        db.add(WhatsAppMessageLog(
            tenant_id=ticket.tenant_id,
            template_name="omniflow_ticket_assigned",
            recipient_user_id=assignee.id,
            recipient_phone=assignee.phone,
            variables_json=json.dumps(variables),
            status=status,
            error_message=error,
            related_entity_type="ticket",
            related_entity_id=ticket.id,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("WhatsApp send_for_ticket_assigned failed")


def _get_tenant(db, tenant_id):
    from .database import Tenant
    return db.query(Tenant).filter(Tenant.id == tenant_id).first()


def _log_notif_suppressed(db, ticket_id, event_label: str):
    """Log a NOTIFICATION_SUPPRESSED TicketEvent for audit trail."""
    from .database import TicketEvent
    try:
        db.add(TicketEvent(
            ticket_id=ticket_id,
            event_type="NOTIFICATION_SUPPRESSED",
            notes=f"Suppressed: {event_label} (outside office hours)",
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to log NOTIFICATION_SUPPRESSED for ticket=%s", ticket_id)


def notify_ticket_assigned(db, ticket, assignee):
    """Phase 0-D-2  |  1-6: TICKET_ASSIGNED — audience: assignee"""
    tenant = _get_tenant(db, ticket.tenant_id)
    if tenant and not is_within_office_hours(tenant):
        if tenant.suppress_notif_outside_hours:
            _log_notif_suppressed(db, ticket.id, "notify_ticket_assigned")
            return
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
    send_whatsapp_for_ticket_assigned(db, ticket, assignee)


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


def notify_ticket_flagged(db, ticket, actor_id: str, admin_ids: list,
                          manager_ids: list = None, actor_name: str = ""):
    """
    1-6: TICKET_FLAGGED
    Audience (in-app/WS): admin + assignee — unchanged.
    Audience (WhatsApp): admin + direct manager only — not assignee.
    """
    audience = list(set(admin_ids + [ticket.current_assignee_id or ""]))
    audience = [uid for uid in audience if uid]
    broadcast_sync(ticket.tenant_id, audience, TICKET_FLAGGED, {
        "ticket_id":     ticket.id,
        "ticket_title":  ticket.title,
        "flagged_reason": ticket.flagged_reason or "",
        "link":          f"/tickets/{ticket.id}",
    })
    _send_wa_ticket_escalated(db, ticket, admin_ids, manager_ids or [], actor_name)


def _send_wa_ticket_escalated(db, ticket, admin_ids: list, manager_ids: list, actor_name: str):
    """Pipeline 3B — omniflow_ticket_escalated. Never raises."""
    from .database import WhatsAppMessageLog, User
    from .services.msg91 import send_whatsapp_template
    import json
    try:
        wa_recipient_ids = list(set(admin_ids + manager_ids))
        for uid in wa_recipient_ids:
            recipient = db.query(User).filter(User.id == uid).first()
            if not recipient or not recipient.phone:
                continue
            variables = [recipient.name, ticket.title, actor_name or "a team member"]
            status, error = "SKIPPED_UNVERIFIED", None
            if recipient.mobile_verified:
                ok, error = send_whatsapp_template(
                    recipient.phone, "omniflow_ticket_escalated", variables)
                status = "SENT" if ok else "FAILED"
            db.add(WhatsAppMessageLog(
                tenant_id=ticket.tenant_id,
                template_name="omniflow_ticket_escalated",
                recipient_user_id=uid,
                recipient_phone=recipient.phone,
                variables_json=json.dumps(variables),
                status=status,
                error_message=error,
                related_entity_type="ticket",
                related_entity_id=ticket.id,
            ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("_send_wa_ticket_escalated failed for ticket=%s", ticket.id)


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


def notify_delay_logged(db, ticket, actor_id: str, reason: str,
                        admin_ids: list, manager_ids: list):
    """E-01: DELAY_LOGGED — notify managers and admins when an assignee logs a delay."""
    audience = list(set(admin_ids + manager_ids))
    for uid in audience:
        create_notification(
            db, ticket.tenant_id, uid,
            "DELAY_LOGGED",
            f"Delay logged on {ticket.display_id or ticket.title}",
            reason[:200],
            f"/tickets/{ticket.id}",
        )


def notify_checklist_due(db, assignment):
    """Phase 0-D-3  |  1-6: CHECKLIST_DUE_SOON — audience: assigned user"""
    tenant = _get_tenant(db, assignment.tenant_id)
    if tenant and not is_within_office_hours(tenant):
        if tenant.suppress_notif_outside_hours:
            return  # Suppressed — no audit trail needed for scheduler-fired reminders
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
    tenant = _get_tenant(db, assignment.tenant_id)
    if tenant and not is_within_office_hours(tenant):
        if tenant.suppress_notif_outside_hours:
            return
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


def notify_checklist_assigned(db, assignment):
    """E-15: Notify employee when a new checklist is assigned to them (if tenant setting enabled)."""
    tenant = _get_tenant(db, assignment.tenant_id)
    if not getattr(tenant, 'checklist_notif_on_assign', True):
        return
    if tenant and not is_within_office_hours(tenant):
        if tenant.suppress_notif_outside_hours:
            return
    title = assignment.template.title if assignment.template else "Checklist"
    due_str = assignment.due_at.strftime("%d %b") if assignment.due_at else ""
    create_notification(
        db, assignment.tenant_id, assignment.user_id,
        "CHECKLIST_DUE",
        f"New checklist assigned: {title}",
        f"Due: {due_str}" if due_str else "",
        "/checklists",
    )


def notify_fms_ticket_opened(db, fms_ticket, assignee, admin_ids: list, manager_ids: list):
    """E-15: In-app notification when a new FMS ticket is opened."""
    tenant = _get_tenant(db, fms_ticket.tenant_id)
    if not getattr(tenant, 'fms_notif_on_open', True):
        return
    if tenant and not is_within_office_hours(tenant):
        if tenant.suppress_notif_outside_hours:
            return
    audience = list(set(admin_ids + manager_ids + ([assignee.id] if assignee else [])))
    for uid in audience:
        if uid:
            create_notification(
                db, fms_ticket.tenant_id, uid,
                "TICKET_ASSIGNED",
                f"New FMS ticket: {fms_ticket.title}",
                f"Flow: {getattr(fms_ticket, 'flow_name', '')}",
                f"/fms/tickets/{fms_ticket.id}",
            )


# ── Phase 2/4 stubs — routing logic defined now (per §18.2 plan) ─────────────

def send_whatsapp_for_fms_stage_transition(db, tenant_id: str, ticket_id: str,
                                            ticket_title: str, stage_name: str, assignee):
    """
    WhatsApp send for omniflow_fms_stage_transition — fires when a ticket
    moves to a new stage and the incoming assignee is known.
    Never raises — always logs an attempt row.
    """
    from .database import WhatsAppMessageLog
    from .services.msg91 import send_whatsapp_template
    variables = [assignee.name, ticket_title, stage_name]
    try:
        if not assignee.mobile_verified:
            status, error = "SKIPPED_UNVERIFIED", None
        else:
            success, error = send_whatsapp_template(
                assignee.phone, "omniflow_fms_stage_transition", variables)
            status = "SENT" if success else "FAILED"
        db.add(WhatsAppMessageLog(
            tenant_id=tenant_id,
            template_name="omniflow_fms_stage_transition",
            recipient_user_id=assignee.id,
            recipient_phone=assignee.phone,
            variables_json=json.dumps(variables),
            status=status,
            error_message=error,
            related_entity_type="fms_ticket",
            related_entity_id=ticket_id,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("WhatsApp fms_stage_transition failed")


def send_whatsapp_for_fms_ticket_created(db, fms_ticket, assignee):
    """
    WhatsApp send on FMS ticket creation — reuses omniflow_ticket_assigned
    (same template, same variables) so no new Meta approval needed.
    Never raises — always logs an attempt row.
    """
    from .database import WhatsAppMessageLog
    from .services.msg91 import send_whatsapp_template, format_wa_date
    due_str = format_wa_date(fms_ticket.due_at) if fms_ticket.due_at else "N/A"
    variables = [assignee.name, fms_ticket.title, fms_ticket.priority, due_str]
    try:
        if not assignee.mobile_verified:
            status, error = "SKIPPED_UNVERIFIED", None
        else:
            success, error = send_whatsapp_template(
                assignee.phone, "omniflow_ticket_assigned", variables)
            status = "SENT" if success else "FAILED"
        db.add(WhatsAppMessageLog(
            tenant_id=fms_ticket.tenant_id,
            template_name="omniflow_ticket_assigned",
            recipient_user_id=assignee.id,
            recipient_phone=assignee.phone,
            variables_json=json.dumps(variables),
            status=status,
            error_message=error,
            related_entity_type="fms_ticket",
            related_entity_id=fms_ticket.id,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("WhatsApp fms_ticket_created failed")


def notify_fms_stage_transition(tenant_id: str, ticket_id: str, ticket_title: str,
                                 new_stage: str, actor_id: str,
                                 admin_ids: list, manager_ids: list, new_assignee_id: str):
    """
    1-6: FMS_STAGE_TRANSITION (used in Phase 2)
    Audience: admin + scoped managers + new assignee.
    WhatsApp is sent separately via send_whatsapp_for_fms_stage_transition().
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
