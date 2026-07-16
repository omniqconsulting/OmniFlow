"""
Collections & Escalation Engine — Workstream A, Phase A4, Req #18.
Auto-generated daily follow-up task list for open collections cases.

Reuses the existing Delegations ticket-generation pattern (Ticket rows with
ticket_type="D", the same display_id increment dance as every other insert
site in app/main.py) rather than building a parallel task system — per the
standing rule to reuse existing module patterns and NOT modify Delegations
internals. This file only ever INSERTs Ticket rows; it never touches
app/main.py's ticket routes or the Ticket model itself.
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("collections_tasks")

_MARKER = "[collections_case:{customer_id}]"


def generate_collections_followup_tasks(db):
    """Scheduled entry point (registered in scheduler.py). For every open
    collections case, ensures exactly one Delegation-style follow-up ticket
    exists per day — dedup via a marker string in the description, since
    Ticket has no dedicated case/customer FK column."""
    from .database import Tenant, Customer, User, Ticket
    from .constants import has_feature
    from .notifications import notify_ticket_assigned

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()

    for tenant in tenants:
        if not has_feature(tenant, "COLLECTIONS_MODULE", db):
            continue

        cases = db.query(Customer).filter(
            Customer.tenant_id == tenant.id,
            Customer.is_deleted == False,
            Customer.open_balance_lock == True,
        ).all()
        if not cases:
            continue

        fallback_admin = db.query(User).filter(
            User.tenant_id == tenant.id, User.role == "ADMIN",
            User.is_active == True, User.is_deleted == False,
        ).first()

        for customer in cases:
            marker = _MARKER.format(customer_id=customer.id)
            existing = db.query(Ticket).filter(
                Ticket.tenant_id == tenant.id,
                Ticket.ticket_type == "D",
                Ticket.created_at >= today_start,
                Ticket.description.like(f"%{marker}%"),
                Ticket.is_deleted == False,
            ).first()
            if existing:
                continue

            assignee = customer.assigned_agent or fallback_admin
            if not assignee:
                continue  # no one to assign to yet — skip rather than create an orphan task

            try:
                ticket = Ticket(
                    tenant_id=tenant.id,
                    title=f"Follow up: {customer.name} (Collections)",
                    description=f"Daily collections follow-up for {customer.name}. {marker}",
                    priority="HIGH" if customer.collections_escalated else "MEDIUM",
                    ticket_type="D",
                    created_by_id=fallback_admin.id if fallback_admin else assignee.id,
                    current_assignee_id=assignee.id,
                    due_at=today_start + timedelta(hours=18),
                )
                db.add(ticket)
                db.flush()
                tenant.ticket_seq = (tenant.ticket_seq or 0) + 1
                ticket.display_id = f"T-{tenant.ticket_seq:04d}"
                db.commit()
                notify_ticket_assigned(db, ticket, assignee)
            except Exception:
                db.rollback()
                logger.exception("generate_collections_followup_tasks failed for customer=%s", customer.id)
