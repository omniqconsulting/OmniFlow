"""
Collections & Escalation Engine — Workstream A, Phase A3.
Automated notification wrappers, all non-blocking (never raise back into the
caller — every failure path is logged and swallowed, per the standing rule
that WhatsApp/SMS/Email sends must never block the underlying user action).

Channel notes:
  - WhatsApp goes through app/services/gupshup.py (the existing single
    integration point) directly, since these messages target the party
    (a Customer) rather than an internal User — the employee opt-in gate in
    notifications.py._send_gupshup_wa doesn't apply here. Sends are logged to
    the same WhatsAppMessageLog table as every other pipeline.
  - SMS and Email have no existing provider integration anywhere in this
    codebase (confirmed: msg91.py is WhatsApp-only). Rather than fabricate a
    new external integration, these two channels are wired as gated no-ops
    that log a clear SKIPPED_NOT_INTEGRATED reason — the toggle in Setup >
    Collections is honored, and wiring a real provider later only needs the
    body of _send_collections_sms / _send_collections_email filled in.
"""
import logging
from datetime import datetime, date

logger = logging.getLogger("collections_notify")


def _owner_users(db, tenant_id):
    """'Owner' = the tenant's active ADMIN users (no separate OWNER role exists)."""
    from .database import User
    return db.query(User).filter(
        User.tenant_id == tenant_id,
        User.role == "ADMIN",
        User.is_active == True,
        User.is_deleted == False,
    ).all()


def _send_collections_wa(db, tenant, phone, recipient_user_id, template_name, variables, related_entity_id):
    """Shared send + log for a Collections WhatsApp template. Never raises."""
    from .database import WhatsAppMessageLog
    from .services.gupshup import send_whatsapp_template
    try:
        if not phone:
            status, error, template_id, template_category, gupshup_message_id, raw_response = "SKIPPED_NO_PHONE", None, None, None, None, None
        else:
            success, error, template_id, template_category, gupshup_message_id, raw_response = send_whatsapp_template(
                tenant, phone, template_name, variables)
            status = "SENT" if success else "FAILED"
        raw_payloads = []
        if gupshup_message_id:
            raw_payloads.append({"id": gupshup_message_id})
        if raw_response:
            raw_payloads.append({"send_response": raw_response})
        db.add(WhatsAppMessageLog(
            tenant_id=tenant.id,
            template_name=template_name,
            recipient_user_id=recipient_user_id,
            recipient_phone=phone or "",
            variables_json=__import__("json").dumps(variables),
            status=status,
            error_message=error,
            related_entity_type="customer",
            related_entity_id=related_entity_id,
            template_id=template_id,
            template_category=template_category,
            raw_status_webhook_payloads=raw_payloads,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Collections WhatsApp send failed for template=%s tenant=%s", template_name, tenant.id if tenant else None)


def _send_collections_sms(db, tenant, phone, template_name, variables, related_entity_id):
    """No SMS provider is integrated in this codebase yet. Honors the
    channel toggle and logs the attempt; never raises. Fill in a real
    provider call here when one is provisioned."""
    try:
        logger.info(
            "Collections SMS skipped (no provider integrated): template=%s tenant=%s phone=%s entity=%s",
            template_name, tenant.id if tenant else None, phone, related_entity_id,
        )
    except Exception:
        logger.exception("Collections SMS logging failed for template=%s", template_name)


def _send_collections_email(db, tenant, email, template_name, variables, related_entity_id):
    """No email provider is integrated in this codebase yet. Honors the
    channel toggle and logs the attempt; never raises. Fill in a real
    provider call here when one is provisioned."""
    try:
        logger.info(
            "Collections email skipped (no provider integrated): template=%s tenant=%s email=%s entity=%s",
            template_name, tenant.id if tenant else None, email, related_entity_id,
        )
    except Exception:
        logger.exception("Collections email logging failed for template=%s", template_name)


def send_payment_reminder_to_party(db, tenant, customer):
    """Req #9 — due/overdue payment reminder to the party, via whichever
    channels the tenant has enabled. Never raises."""
    try:
        days_overdue = (date.today() - customer.collections_case_due_date).days if customer.collections_case_due_date else 0
        due_str = customer.collections_case_due_date.strftime("%d %b %Y") if customer.collections_case_due_date else "N/A"
        variables = [customer.name, due_str, str(max(days_overdue, 0))]
        if getattr(tenant, "collections_channel_whatsapp_enabled", False):
            _send_collections_wa(db, tenant, customer.phone, None,
                                  "omniflow_collections_payment_reminder", variables, customer.id)
        if getattr(tenant, "collections_channel_sms_enabled", False):
            _send_collections_sms(db, tenant, customer.phone,
                                   "omniflow_collections_payment_reminder", variables, customer.id)
        if getattr(tenant, "collections_channel_email_enabled", False):
            _send_collections_email(db, tenant, customer.email,
                                     "omniflow_collections_payment_reminder", variables, customer.id)
    except Exception:
        logger.exception("send_payment_reminder_to_party failed for customer=%s", customer.id)


def notify_owner_overdue(db, tenant, customer):
    """Req #10 — owner notification on payment overdue. Configurable-but-off:
    planning marked this "not needed" but it remains a numbered client
    requirement, so it's gated behind Setup > Collections >
    collections_owner_notify_enabled (default False) rather than omitted."""
    if not getattr(tenant, "collections_owner_notify_enabled", False):
        return
    try:
        days_overdue = (date.today() - customer.collections_case_due_date).days if customer.collections_case_due_date else 0
        for owner in _owner_users(db, tenant.id):
            variables = [owner.name, customer.name, str(max(days_overdue, 0))]
            _notify_owners_single(db, tenant, customer, owner, "omniflow_collections_owner_overdue", variables)
    except Exception:
        logger.exception("notify_owner_overdue failed for customer=%s", customer.id)


def _notify_owners_single(db, tenant, customer, owner, template_name, variables):
    if getattr(tenant, "collections_channel_whatsapp_enabled", False):
        _send_collections_wa(db, tenant, owner.phone, owner.id, template_name, variables, customer.id)
    if getattr(tenant, "collections_channel_sms_enabled", False):
        _send_collections_sms(db, tenant, owner.phone, template_name, variables, customer.id)
    if getattr(tenant, "collections_channel_email_enabled", False):
        _send_collections_email(db, tenant, owner.email, template_name, variables, customer.id)


def notify_owner_escalation_tier(db, tenant, customer, tier_days):
    """Req #11 — tiered escalation notice to owner at 30/60/90 days overdue.
    Not part of the "not needed" open decision (only #10/#12 are), so this
    fires unconditionally through whichever channels are enabled."""
    try:
        for owner in _owner_users(db, tenant.id):
            variables = [owner.name, customer.name, str(tier_days)]
            _notify_owners_single(db, tenant, customer, owner, "omniflow_collections_escalation_tier", variables)
    except Exception:
        logger.exception("notify_owner_escalation_tier failed for customer=%s", customer.id)


def notify_owner_payment_received(db, tenant, customer):
    """Req #12 — payment-received confirmation to owner. Configurable-but-off,
    same open-decision treatment as Req #10."""
    if not getattr(tenant, "collections_owner_notify_enabled", False):
        return
    try:
        for owner in _owner_users(db, tenant.id):
            variables = [owner.name, customer.name]
            _notify_owners_single(db, tenant, customer, owner, "omniflow_collections_payment_received", variables)
    except Exception:
        logger.exception("notify_owner_payment_received failed for customer=%s", customer.id)


def notify_owner_non_responsive(db, tenant, customer):
    """Req #14 — non-responsive-party alert to owner. Fires once per case
    (collections_non_responsive_alerted dedup) when the case has escalated
    and the party has never once been reached (no CONNECTED call logged)."""
    try:
        for owner in _owner_users(db, tenant.id):
            variables = [owner.name, customer.name, str(customer.collections_call_attempt_count or 0)]
            _notify_owners_single(db, tenant, customer, owner, "omniflow_collections_non_responsive", variables)
    except Exception:
        logger.exception("notify_owner_non_responsive failed for customer=%s", customer.id)


def log_follow_up_missed_to_system(db, tenant, customer, agent):
    """Req #13 — follow-up-missed alert routed to the CRM system log (in-app
    notification to the assigned agent), per the clarified requirement that
    this must NOT go to the owner. Reuses the existing in-app notification
    pipe rather than a new "system log" table."""
    from .notifications import create_notification
    try:
        if not agent:
            return
        create_notification(
            db, tenant.id, agent.id,
            notif_type="COLLECTIONS_FOLLOWUP_MISSED",
            title=f"Missed follow-up — {customer.name}",
            body=f"A scheduled follow-up for {customer.name}'s collections case is overdue.",
            link=f"/sales/contacts/{customer.id}",
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("log_follow_up_missed_to_system failed for customer=%s", customer.id)


def run_daily_collections_notifications(db):
    """Scheduled entry point (registered in scheduler.py). Iterates every
    tenant with COLLECTIONS_MODULE enabled and every open case, firing the
    Req #9/#10/#11/#14 notifications with per-case dedup so tiered/
    non-responsive alerts don't repeat every day, then handles Req #13
    (missed follow-ups) separately since it isn't tied to due-date tiers."""
    from .database import Tenant, Customer, CRMCallLog, User
    from .constants import has_feature
    from sqlalchemy import func as _func

    today = date.today()
    tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
    for tenant in tenants:
        if not has_feature(tenant, "COLLECTIONS_MODULE", db):
            continue

        tiers = sorted(set(
            int(t.strip()) for t in (tenant.collections_escalation_tiers or "30,60,90").split(",")
            if t.strip().isdigit()
        ))

        cases = db.query(Customer).filter(
            Customer.tenant_id == tenant.id,
            Customer.is_deleted == False,
            Customer.open_balance_lock == True,
        ).all()

        for customer in cases:
            send_payment_reminder_to_party(db, tenant, customer)

            days_overdue = (today - customer.collections_case_due_date).days if customer.collections_case_due_date else None
            if days_overdue is not None and days_overdue > 0:
                notify_owner_overdue(db, tenant, customer)

            if days_overdue is not None:
                reached_tier = max([t for t in tiers if days_overdue >= t], default=None)
                if reached_tier and reached_tier > (customer.collections_last_tier_notified or 0):
                    notify_owner_escalation_tier(db, tenant, customer, reached_tier)
                    customer.collections_last_tier_notified = reached_tier
                    db.commit()

            if customer.collections_escalated and not customer.collections_non_responsive_alerted:
                ever_connected = db.query(CRMCallLog).filter(
                    CRMCallLog.customer_id == customer.id,
                    CRMCallLog.outcome == "CONNECTED",
                ).first()
                if not ever_connected:
                    notify_owner_non_responsive(db, tenant, customer)
                    customer.collections_non_responsive_alerted = True
                    db.commit()

        # Req #13 — missed follow-ups on open collections cases, to the
        # assigned agent's in-app log, never the owner.
        missed = db.query(CRMCallLog).join(Customer, CRMCallLog.customer_id == Customer.id).filter(
            CRMCallLog.tenant_id == tenant.id,
            Customer.open_balance_lock == True,
            Customer.is_deleted == False,
            CRMCallLog.follow_up_done == False,
            CRMCallLog.follow_up_at != None,
            _func.date(CRMCallLog.follow_up_at) <= today,
        ).all()
        for call_log in missed:
            customer = db.query(Customer).get(call_log.customer_id)
            agent = db.query(User).get(call_log.agent_id)
            log_follow_up_missed_to_system(db, tenant, customer, agent)
