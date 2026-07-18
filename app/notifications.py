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


def add_business_hours(tenant, start_dt, hours):
    """Forward counterpart to business_hours_elapsed: return the datetime
    that is `hours` of business hours after start_dt, respecting the
    tenant's configured work days/hours. Used for TaT/due-date planning so a
    ticket opened at 5pm doesn't get credited with overnight/weekend hours
    toward its TaT — the clock only runs during office hours.

    Falls back to raw wall-clock arithmetic if office hours aren't
    configured, matching business_hours_elapsed's fallback. Returns a naive
    UTC datetime (same convention as the rest of the codebase)."""
    if hours is None:
        return start_dt
    if not getattr(tenant, 'work_start_time', None):
        return start_dt + timedelta(hours=hours)
    try:
        import pytz
        tz = pytz.timezone(tenant.timezone or 'Asia/Kolkata')
    except Exception:
        return start_dt + timedelta(hours=hours)

    work_days = [int(d) for d in (tenant.work_days or '0,1,2,3,4').split(',') if d.strip()]
    start_h, start_m = map(int, tenant.work_start_time.split(':'))
    end_h, end_m = map(int, tenant.work_end_time.split(':'))
    if not work_days:
        return start_dt + timedelta(hours=hours)

    naive = start_dt.tzinfo is None
    dt = pytz.utc.localize(start_dt) if naive else start_dt
    cursor = dt.astimezone(tz)
    remaining_minutes = hours * 60

    # If the start point falls outside office hours (evening/weekend/before
    # opening), jump forward to the next working window before the clock
    # starts running — this is the fix for "opened in the evening" tickets.
    for _ in range(400):  # hard cap — defends against pathological configs
        day_start = cursor.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        day_end = cursor.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        if cursor.weekday() not in work_days or cursor >= day_end:
            cursor = (cursor + timedelta(days=1)).replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            continue
        if cursor < day_start:
            cursor = day_start

        available_today = (day_end - cursor).total_seconds() / 60
        if remaining_minutes <= available_today:
            cursor = cursor + timedelta(minutes=remaining_minutes)
            remaining_minutes = 0
            break
        remaining_minutes -= available_today
        cursor = (cursor + timedelta(days=1)).replace(hour=start_h, minute=start_m, second=0, microsecond=0)

    result = cursor.astimezone(pytz.utc)
    return result.replace(tzinfo=None) if naive else result

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
    # Web Push — third, additive channel alongside in-app + WhatsApp (Phase 6)
    try:
        from .push import send_push_for_user
        send_push_for_user(db, user_id, title, body, link)
    except Exception:
        logger.warning("Web push send skipped for user %s", user_id, exc_info=True)


_OPTED_IN_STATUSES = ("OPTED_IN", "MANUALLY_VERIFIED")

# Setup > Notifications > WhatsApp — maps each pipeline's event_key to the
# tenant column that toggles it on/off. Any event_key not listed here always
# sends (no toggle exists for it yet).
_WA_EVENT_TOGGLE_FIELD = {
    "ticket_assigned":       "wa_notif_ticket_assigned",
    "ticket_escalated":      "wa_notif_ticket_escalated",
    "fms_ticket_created":    "wa_notif_fms_ticket_created",
    "fms_stage_transition":  "wa_notif_fms_stage_transition",
    "order_placed":          "wa_notif_order_placed",
    "order_dispatched":      "wa_notif_order_dispatched",
    "ticket_closed":         "wa_notif_ticket_closed",
    "ticket_tat_reminder":   "wa_notif_ticket_tat_reminder",
    "fms_ticket_closed":     "wa_notif_fms_ticket_closed",
    "fms_ticket_flagged":    "wa_notif_fms_ticket_flagged",
    "po_placed":             "wa_notif_po_placed",
    "po_accepted":           "wa_notif_po_accepted",
}


def _send_gupshup_wa(db, tenant_id, recipient, template_name, variables,
                      related_entity_type=None, related_entity_id=None,
                      event_key=None):
    """
    Shared gate + send + log for a single-recipient WhatsApp template send,
    routed through the tenant's own Gupshup WABA — replaces the old per-pipeline
    mobile_verified + msg91 pattern duplicated across this file (Gupshup
    migration brief, Decision #12 / Section 4.2). Never raises.

    event_key: key into _WA_EVENT_TOGGLE_FIELD — lets Setup > Notifications
    turn this specific WhatsApp event off per tenant without touching the
    in-app notification it rides alongside.
    """
    from .database import WhatsAppMessageLog, Tenant
    from .services.gupshup import send_whatsapp_template
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        template_id = template_category = gupshup_message_id = raw_response = None
        toggle_field = _WA_EVENT_TOGGLE_FIELD.get(event_key)
        if toggle_field and tenant is not None and getattr(tenant, toggle_field, True) is False:
            status, error = "SKIPPED_DISABLED", None
        elif not recipient or getattr(recipient, "whatsapp_opt_in_status", None) not in _OPTED_IN_STATUSES:
            status, error = "SKIPPED_UNVERIFIED", None
        elif getattr(recipient, "whatsapp_notifications_enabled", True) is False:
            # Employee has verified but chosen to turn WhatsApp notifications
            # off for themselves (Employees tab) — distinct from opt-in status.
            status, error = "SKIPPED_BY_EMPLOYEE", None
        else:
            success, error, template_id, template_category, gupshup_message_id, raw_response = send_whatsapp_template(
                tenant, recipient.phone, template_name, variables)
            status = "SENT" if success else "FAILED"
        # Seed raw_status_webhook_payloads with the send-time message id (so
        # inbound status webhooks in Section 6.3 can be matched back to this
        # row) and Gupshup's full raw response (for debugging sends that
        # report success but never actually reach the recipient).
        raw_payloads = []
        if gupshup_message_id:
            raw_payloads.append({"id": gupshup_message_id})
        if raw_response:
            raw_payloads.append({"send_response": raw_response})
        db.add(WhatsAppMessageLog(
            tenant_id=tenant_id,
            template_name=template_name,
            recipient_user_id=recipient.id if recipient else None,
            recipient_phone=recipient.phone if recipient else "",
            variables_json=json.dumps(variables),
            status=status,
            error_message=error,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            template_id=template_id,
            template_category=template_category,
            raw_status_webhook_payloads=raw_payloads,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("_send_gupshup_wa failed for template=%s tenant=%s", template_name, tenant_id)


def send_whatsapp_for_optin_confirmed(db, tenant_id, employee):
    """
    omniflow_optin_confirmed — sent once, immediately after the Gupshup
    webhook flips an employee PENDING/MISMATCH -> OPTED_IN, confirming
    enrollment. Same send pipeline as every other notification (per-tenant
    Gupshup credentials, opt-in gate, WhatsAppMessageLog). Never raises.
    """
    try:
        variables = [employee.name]
        _send_gupshup_wa(db, tenant_id, employee, "omniflow_optin_confirmed", variables,
                          related_entity_type="user", related_entity_id=employee.id)
    except Exception:
        logger.exception("send_whatsapp_for_optin_confirmed failed for user=%s", employee.id)


def send_whatsapp_for_ticket_assigned(db, ticket, assignee):
    """
    WhatsApp send for ticket_assigned — Pipeline 1.
    Never allowed to raise back into the caller.
    Always logs an attempt row regardless of outcome.
    """
    from .services.msg91 import format_wa_date

    due_str = format_wa_date(ticket.due_at) if ticket.due_at else "N/A"
    variables = [assignee.name, ticket.title, ticket.priority, due_str]
    _send_gupshup_wa(db, ticket.tenant_id, assignee, "omniflow_ticket_assigned", variables,
                      related_entity_type="ticket", related_entity_id=ticket.id,
                      event_key="ticket_assigned")


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
    if new_status == "CLOSED" and old_status != "CLOSED":
        _send_wa_ticket_closed(db, ticket, actor_id)


def _send_wa_ticket_closed(db, ticket, actor_id: str):
    """omniflow_ticket_closed — notify the assignee their ticket was closed. Never raises."""
    from .database import User
    try:
        if not ticket.current_assignee_id:
            return
        assignee = db.query(User).filter(User.id == ticket.current_assignee_id).first()
        if not assignee:
            return
        actor = db.query(User).filter(User.id == actor_id).first() if actor_id else None
        variables = [assignee.name, ticket.title, actor.name if actor else "an admin"]
        _send_gupshup_wa(db, ticket.tenant_id, assignee, "omniflow_ticket_closed", variables,
                          related_entity_type="ticket", related_entity_id=ticket.id,
                          event_key="ticket_closed")
    except Exception:
        logger.exception("_send_wa_ticket_closed failed for ticket=%s", ticket.id)


def send_whatsapp_for_ticket_tat_reminder(db, ticket, recipient, assignee_name, hours_or_pct):
    """
    Reuses omniflow_ticket_unacknowledged for the TAT % elapsed reminder
    (Setup > Notifications > ticket_notif_tat_pct / _both). Never raises —
    always logs an attempt row via _send_gupshup_wa.
    """
    try:
        variables = [recipient.name, ticket.title, assignee_name, str(hours_or_pct)]
        _send_gupshup_wa(db, ticket.tenant_id, recipient, "omniflow_ticket_unacknowledged", variables,
                          related_entity_type="ticket", related_entity_id=ticket.id,
                          event_key="ticket_tat_reminder")
    except Exception:
        logger.exception("send_whatsapp_for_ticket_tat_reminder failed for ticket=%s", ticket.id)


def send_whatsapp_for_fms_ticket_closed(db, tenant_id, fms_ticket, admin_ids, manager_ids, actor_name):
    """omniflow_fms_ticket_closed — notify admins/managers. Never raises."""
    from .database import User
    try:
        recipient_ids = list(set((admin_ids or []) + (manager_ids or [])))
        for uid in recipient_ids:
            recipient = db.query(User).filter(User.id == uid).first()
            if not recipient or not recipient.phone:
                continue
            variables = [recipient.name, fms_ticket.title, actor_name or "a team member"]
            _send_gupshup_wa(db, tenant_id, recipient, "omniflow_fms_ticket_closed", variables,
                              related_entity_type="fms_ticket", related_entity_id=fms_ticket.id,
                              event_key="fms_ticket_closed")
    except Exception:
        logger.exception("send_whatsapp_for_fms_ticket_closed failed for ticket=%s", fms_ticket.id)


def send_whatsapp_for_fms_ticket_flagged(db, tenant_id, fms_ticket, admin_ids, manager_ids,
                                          flag_reason, actor_name):
    """omniflow_fms_ticket_flagged — notify admins/managers. Never raises."""
    from .database import User
    try:
        recipient_ids = list(set((admin_ids or []) + (manager_ids or [])))
        for uid in recipient_ids:
            recipient = db.query(User).filter(User.id == uid).first()
            if not recipient or not recipient.phone:
                continue
            variables = [recipient.name, fms_ticket.title, flag_reason or "No reason given"]
            _send_gupshup_wa(db, tenant_id, recipient, "omniflow_fms_ticket_flagged", variables,
                              related_entity_type="fms_ticket", related_entity_id=fms_ticket.id,
                              event_key="fms_ticket_flagged")
    except Exception:
        logger.exception("send_whatsapp_for_fms_ticket_flagged failed for ticket=%s", fms_ticket.id)


def send_whatsapp_for_po_placed(db, tenant_id, po, admin_ids):
    """omniflow_po_placed — notify admins that a PO was submitted to a vendor. Never raises."""
    from .database import User
    try:
        for uid in admin_ids or []:
            recipient = db.query(User).filter(User.id == uid).first()
            if not recipient or not recipient.phone:
                continue
            variables = [recipient.name, po.display_id, po.vendor_name_snapshot or "vendor"]
            _send_gupshup_wa(db, tenant_id, recipient, "omniflow_po_placed", variables,
                              related_entity_type="purchase_order", related_entity_id=po.id,
                              event_key="po_placed")
    except Exception:
        logger.exception("send_whatsapp_for_po_placed failed for po=%s", po.id)


def send_whatsapp_for_po_accepted(db, tenant_id, po):
    """omniflow_po_accepted — notify the PO's creator that it was approved. Never raises."""
    from .database import User
    try:
        if not po.created_by_id:
            return
        recipient = db.query(User).filter(User.id == po.created_by_id).first()
        if not recipient or not recipient.phone:
            return
        variables = [recipient.name, po.display_id, po.vendor_name_snapshot or "vendor"]
        _send_gupshup_wa(db, tenant_id, recipient, "omniflow_po_accepted", variables,
                          related_entity_type="purchase_order", related_entity_id=po.id,
                          event_key="po_accepted")
    except Exception:
        logger.exception("send_whatsapp_for_po_accepted failed for po=%s", po.id)


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
    from .database import User
    try:
        wa_recipient_ids = list(set(admin_ids + manager_ids))
        for uid in wa_recipient_ids:
            recipient = db.query(User).filter(User.id == uid).first()
            if not recipient or not recipient.phone:
                continue
            variables = [recipient.name, ticket.title, actor_name or "a team member"]
            _send_gupshup_wa(db, ticket.tenant_id, recipient, "omniflow_ticket_escalated", variables,
                              related_entity_type="ticket", related_entity_id=ticket.id,
                              event_key="ticket_escalated")
    except Exception:
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
    variables = [assignee.name, ticket_title, stage_name]
    _send_gupshup_wa(db, tenant_id, assignee, "omniflow_fms_stage_transition", variables,
                      related_entity_type="fms_ticket", related_entity_id=ticket_id,
                      event_key="fms_stage_transition")


def send_whatsapp_for_fms_ticket_created(db, fms_ticket, assignee):
    """
    WhatsApp send on FMS ticket creation — reuses omniflow_ticket_assigned
    (same template, same variables) so no new Meta approval needed.
    Never raises — always logs an attempt row.
    """
    from .services.msg91 import format_wa_date
    due_str = format_wa_date(fms_ticket.due_at) if fms_ticket.due_at else "N/A"
    variables = [assignee.name, fms_ticket.title, fms_ticket.priority, due_str]
    _send_gupshup_wa(db, fms_ticket.tenant_id, assignee, "omniflow_ticket_assigned", variables,
                      related_entity_type="fms_ticket", related_entity_id=fms_ticket.id,
                      event_key="fms_ticket_created")


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


def notify_fms_split_created(tenant_id: str, ticket_id: str, ticket_display_id: str,
                              split_display_id: str, stage_name: str, actor_id: str,
                              admin_ids: list, manager_ids: list, new_assignee_id: str):
    """
    FMS Auto-Split Engine (brief §5/§9-E): real-time broadcast fired whenever
    the split engine auto-creates a moved-forward split. Mirrors
    notify_fms_stage_transition's audience pattern (admin + scoped managers +
    new assignee). Never raises — broadcast_sync is already fire-and-forget;
    callers must still wrap this call in try/except (see app/fms.py) so a
    WS-layer failure can never block split creation.
    """
    from .ws_manager import SPLIT_CREATED
    audience = list(set((admin_ids or []) + (manager_ids or []) + [new_assignee_id or ""]))
    audience = [uid for uid in audience if uid]
    broadcast_sync(tenant_id, audience, SPLIT_CREATED, {
        "ticket_id":         ticket_id,
        "ticket_display_id": ticket_display_id,
        "split_display_id":  split_display_id,
        "stage_name":        stage_name,
        "actor_id":          actor_id,
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


# ── Brief 5: Sales Orders ─────────────────────────────────────────────────────

def _send_wa_order_placed(db, staff, order):
    """omniflow_order_placed. Never raises — always logs an attempt row."""
    variables = [staff.name, order.display_id, order.customer.name, str(len(order.items))]
    _send_gupshup_wa(db, order.tenant_id, staff, "omniflow_order_placed", variables,
                      related_entity_type="sales_order", related_entity_id=order.id,
                      event_key="order_placed")


def notify_order_placed(db, order):
    """Notify godown (INVENTORY module) staff and tenant Admins that an order was confirmed."""
    from .database import User
    from .auth import has_module

    godown_staff = [u for u in db.query(User).filter(
        User.tenant_id == order.tenant_id,
        User.is_active == True, User.is_deleted == False,
    ).all() if has_module(u, "INVENTORY")]

    for staff in godown_staff:
        create_notification(
            db=db, tenant_id=order.tenant_id, user_id=staff.id,
            notif_type="ORDER_PLACED",
            title=f"New order confirmed: {order.display_id}",
            body=f"Customer: {order.customer.name} · {len(order.items)} item(s) · "
                 f"₹{order.total_amount:,.0f}",
            link="/inventory-v2/dispatch-queue",
        )
        _send_wa_order_placed(db, staff, order)

    admins = db.query(User).filter(
        User.tenant_id == order.tenant_id,
        User.role == "ADMIN", User.is_deleted == False,
    ).all()
    for admin in admins:
        create_notification(
            db=db, tenant_id=order.tenant_id, user_id=admin.id,
            notif_type="ORDER_PLACED",
            title=f"Order placed: {order.display_id}",
            body=f"{order.agent.name} → {order.customer.name} · ₹{order.total_amount:,.0f}",
            link=f"/sales/orders/{order.id}",
        )
    db.commit()


def _send_wa_order_dispatched(db, order, dispatched_by):
    """omniflow_order_dispatched. Never raises — always logs an attempt row."""
    agent = order.agent
    variables = [agent.name, order.display_id, order.customer.name,
                 datetime.utcnow().strftime("%d %b %Y")]
    _send_gupshup_wa(db, order.tenant_id, agent, "omniflow_order_dispatched", variables,
                      related_entity_type="sales_order", related_entity_id=order.id,
                      event_key="order_dispatched")


def notify_order_dispatched(db, order, dispatched_by):
    """Notify the sales agent that their order has been dispatched."""
    create_notification(
        db=db, tenant_id=order.tenant_id, user_id=order.agent_id,
        notif_type="ORDER_DISPATCHED",
        title=f"Order dispatched: {order.display_id}",
        body=f"Dispatched by {dispatched_by.name}. Customer: {order.customer.name}",
        link=f"/sales/orders/{order.id}",
    )
    db.commit()
    _send_wa_order_dispatched(db, order, dispatched_by)
