"""
Background scheduler — Phase 0-B-1, 0-B-2, 0-B-3/4/5, 0-D-5
"""
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .constants import FMS_INACTIVE_STATUSES

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


# ── Job functions ─────────────────────────────────────────────────────────────

def generate_recurring_checklists():
    """Phase 0-B-1: auto-generate checklist instances for all active templates."""
    from .database import SessionLocal, ChecklistTemplate, ChecklistAssignment, User
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        templates = db.query(ChecklistTemplate).filter(
            ChecklistTemplate.is_active == True,
            ChecklistTemplate.is_deleted == False,
        ).all()

        for tmpl in templates:
            # E-14: custom frequency types — check before falling through to legacy
            from .checklist_freq import CUSTOM_FREQUENCY_TYPES, matches_today, apply_due_time
            ft = getattr(tmpl, "frequency_type", None)
            if ft in CUSTOM_FREQUENCY_TYPES:
                if not matches_today(tmpl, now):
                    continue
                next_due = apply_due_time(now.date(), tmpl)
                if next_due <= now:
                    next_due += timedelta(days=1)
            elif ft in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY", None):
                # NULL frequency_type falls through to legacy field logic below
                pass
            else:
                continue

            if ft is not None and ft not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
                # Custom type handled above — skip to user assignment loop
                pass
            # Determine lookback window and next due time by frequency (legacy)
            elif tmpl.frequency == "DAILY":
                lookback = timedelta(days=1)
                next_due = apply_due_time(now.date(), tmpl)
                if next_due <= now:
                    next_due += timedelta(days=1)
            elif tmpl.frequency == "WEEKLY":
                lookback = timedelta(weeks=1)
                days_ahead = 7 - now.weekday()
                next_due = apply_due_time((now + timedelta(days=days_ahead)).date(), tmpl)
            elif tmpl.frequency == "MONTHLY":
                lookback = timedelta(days=32)
                m = now.month % 12 + 1
                y = now.year + (1 if now.month == 12 else 0)
                next_due = apply_due_time(now.replace(year=y, month=m, day=1).date(), tmpl)
            elif tmpl.frequency == "TWICE_A_MONTH":
                lookback = timedelta(days=16)
                next_due = now + timedelta(days=15)
            elif tmpl.frequency == "QUARTERLY":
                lookback = timedelta(days=92)
                next_due = now + timedelta(days=91)
            elif tmpl.frequency == "YEARLY":
                lookback = timedelta(days=366)
                next_due = now + timedelta(days=365)
            elif tmpl.frequency == "PER_SHIFT":
                lookback = timedelta(hours=8)
                next_due = now + timedelta(hours=8)
            else:
                continue

            # Determine target users
            if tmpl.assigned_to_user_id:
                users = db.query(User).filter(
                    User.id == tmpl.assigned_to_user_id,
                    User.tenant_id == tmpl.tenant_id,
                    User.is_active == True,
                    User.is_deleted == False,
                ).all()
            elif tmpl.assigned_to_dept_id:
                users = db.query(User).filter(
                    User.department_id == tmpl.assigned_to_dept_id,
                    User.tenant_id == tmpl.tenant_id,
                    User.is_active == True,
                    User.is_deleted == False,
                ).all()
            elif tmpl.assigned_to_role:
                users = db.query(User).filter(
                    User.role == tmpl.assigned_to_role,
                    User.tenant_id == tmpl.tenant_id,
                    User.is_active == True,
                    User.is_deleted == False,
                ).all()
            else:
                continue

            for u in users:
                # Skip only if an assignment for this exact due date already exists
                existing = db.query(ChecklistAssignment).filter(
                    ChecklistAssignment.template_id == tmpl.id,
                    ChecklistAssignment.user_id == u.id,
                    ChecklistAssignment.due_at == next_due,
                    ChecklistAssignment.is_deleted == False,
                ).first()
                if not existing:
                    db.add(ChecklistAssignment(
                        template_id=tmpl.id,
                        tenant_id=tmpl.tenant_id,
                        user_id=u.id,
                        due_at=next_due,
                    ))
        db.commit()
    except Exception as e:
        logger.error("generate_recurring_checklists error: %s", e)
        db.rollback()
    finally:
        db.close()


def mark_overdue_checklists():
    """Phase 0-B-2: mark pending/in-progress assignments as OVERDUE every 15 min.
    Overdue is a date concept, not a time-of-day one — a checklist due later today
    isn't overdue just because its due time passed; only a past calendar day counts."""
    from .database import SessionLocal, ChecklistAssignment
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        overdue = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.due_at < today_start,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
        ).all()
        for a in overdue:
            a.status = "OVERDUE"
        if overdue:
            db.commit()
            logger.info("Marked %d checklists OVERDUE", len(overdue))
    except Exception as e:
        logger.error("mark_overdue_checklists error: %s", e)
        db.rollback()
    finally:
        db.close()


def escalate_unacknowledged_tickets():
    """Phase 0-D-5: alert admins/managers for tickets unacknowledged >2 hours."""
    from .database import SessionLocal, Ticket, User
    from .notifications import create_notification, claim_dedup_key
    from .notification_rules import filter_recipients
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        threshold = now - timedelta(hours=2)
        unacked = db.query(Ticket).filter(
            Ticket.status == "OPEN",
            Ticket.created_at < threshold,
            Ticket.acknowledged_at == None,
            Ticket.is_deleted == False,
            Ticket.priority.in_(["HIGH", "CRITICAL"]),
        ).all()

        for ticket in unacked:
            role_ids = db.query(User).filter(
                User.tenant_id == ticket.tenant_id,
                User.role.in_(["ADMIN", "MANAGER"]),
                User.is_deleted == False,
                User.is_active == True,
            ).all()
            admin_ids = [u.id for u in role_ids if u.role == "ADMIN"]
            manager_ids = [u.id for u in role_ids if u.role == "MANAGER"]
            recipient_ids = filter_recipients(
                db, ticket.tenant_id, "ticket_unacknowledged",
                admin_ids=admin_ids, manager_ids=manager_ids,
                assignee_id=ticket.current_assignee_id,
            )
            for uid in recipient_ids:
                # Fire once per ticket per recipient for its whole unacknowledged
                # lifetime -- not a rolling window, which caused repeat spam.
                # Atomic claim (not just a SELECT check) so two scheduler
                # instances racing on the same 30-min tick can't both send it.
                if claim_dedup_key(db, f"ticket_unacknowledged:{uid}:{ticket.id}"):
                    create_notification(
                        db, tenant_id=ticket.tenant_id, user_id=uid,
                        notif_type="TICKET_ESCALATION",
                        title="⚠ Ticket not acknowledged",
                        body=f'"{ticket.title}" has been open for 2+ hours without acknowledgement.',
                        link=f"/tickets/{ticket.id}",
                        condition_key="ticket_unacknowledged",
                    )
        db.commit()
    except Exception as e:
        logger.error("escalate_unacknowledged_tickets error: %s", e)
        db.rollback()
    finally:
        db.close()


# ── Phase 5 jobs ─────────────────────────────────────────────────────────────

def morning_ticket_summary():
    """P5-05: 8:00 AM IST daily — notify each user of their open/in-progress
    tickets due today or overdue. condition_key "ticket_morning_summary"
    (in-app + push + WhatsApp)."""
    from datetime import date as _date
    from .database import SessionLocal, Ticket, User, Notification
    from .notifications import create_notification, _send_gupshup_wa, claim_dedup_key
    from .notification_rules import channel_enabled, get_recipient_roles
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today_end = datetime.combine(_date.today(), datetime.max.time())
        today_key = _date.today().isoformat()

        # Group open tickets by assignee
        open_statuses = ("OPEN", "ACKNOWLEDGED", "IN_PROGRESS")
        tickets = db.query(Ticket).filter(
            Ticket.is_deleted == False,
            Ticket.status.in_(open_statuses),
            Ticket.due_at <= today_end,
        ).all()

        by_user: dict = {}
        for t in tickets:
            by_user.setdefault(t.current_assignee_id, []).append(t)

        for uid, user_tickets in by_user.items():
            if not uid:
                continue
            tenant_id_check = user_tickets[0].tenant_id
            if "ASSIGNEE" not in get_recipient_roles(db, tenant_id_check, "ticket_morning_summary"):
                continue  # this tenant has turned the digest's only possible recipient off
            overdue = [t for t in user_tickets if t.due_at and t.due_at < now]
            due_today = [t for t in user_tickets if t.due_at and t.due_at >= now]
            parts = []
            if overdue:
                parts.append(f"{len(overdue)} overdue")
            if due_today:
                parts.append(f"{len(due_today)} due today")
            if not parts:
                continue
            # Atomic claim (per user, per day) so two scheduler instances
            # both waking on the same daily cron tick can't both send it.
            if not claim_dedup_key(db, f"ticket_morning_summary:{uid}:{today_key}"):
                continue
            tenant_id = user_tickets[0].tenant_id
            body = f"You have {', '.join(parts)} ticket(s) needing attention."
            create_notification(
                db, tenant_id=tenant_id, user_id=uid,
                notif_type="TICKET_REMINDER",
                title="🌅 Morning ticket summary",
                body=body,
                link="/tickets?status=OPEN",
                condition_key="ticket_morning_summary",
            )
            if channel_enabled(db, tenant_id, "ticket_morning_summary", "whatsapp"):
                try:
                    u = db.query(User).filter(User.id == uid).first()
                    if u and u.phone:
                        _send_gupshup_wa(db, tenant_id, u, "omniflow_ticket_morning_summary",
                                          [u.name, body], related_entity_type="ticket_summary",
                                          related_entity_id=uid, event_key="ticket_morning_summary")
                except Exception:
                    logger.exception("Morning summary WhatsApp failed for user=%s", uid)
        db.commit()
        logger.info("Morning ticket summary sent to %d users", len(by_user))
    except Exception as e:
        logger.error("morning_ticket_summary error: %s", e)
        db.rollback()
    finally:
        db.close()


# ── Phase 6 jobs ─────────────────────────────────────────────────────────────

_DEFAULT_NOTIF_HOURS_IST = [8, 13, 18]   # IST hours — fallback when tenant has no custom config

def _build_checklist_titles_csv(assignments: list) -> str:
    """Build the comma-separated checklist title string for body_2.
    Caps at 5 titles; appends '+ X more' if there are additional items.
    Used by both Pipeline 2A (checklist_due) and 2B (checklist_overdue).
    """
    titles = [a.template.title for a in assignments if a.template]
    if not titles:
        return ""
    csv = ", ".join(titles[:5])
    if len(titles) > 5:
        csv += f", +{len(titles) - 5} more"
    return csv


def _parse_notif_hours(raw: str | None) -> list[int]:
    """Parse comma-separated IST hour string into a sorted list of UTC hours for comparison."""
    if not raw:
        ist_hours = _DEFAULT_NOTIF_HOURS_IST
    else:
        try:
            ist_hours = [int(h.strip()) for h in raw.split(",") if h.strip().isdigit()]
            ist_hours = sorted(set(h for h in ist_hours if 0 <= h <= 23))
            if not ist_hours:
                ist_hours = _DEFAULT_NOTIF_HOURS_IST
        except Exception:
            ist_hours = _DEFAULT_NOTIF_HOURS_IST
    # Convert IST → UTC: subtract 5 hours (ignoring :30 offset at hour granularity)
    return [(h - 5) % 24 for h in ist_hours]


def send_consolidated_checklist_notifications():
    """THE checklist reminder job (merges the old per-assignment "remind" job
    and this consolidated job into one — condition_key "checklist_reminder",
    in-app + push only, no WhatsApp).

    Runs every 30 minutes; for each tenant, fires once at each of the
    tenant-configured hours (Setup > Notifications), one notification per
    employee listing everything due **today** — never the overdue backlog
    (that's tracked via status only, not re-reminded here).

    Leave/branch aware: a checklist due on the assignee's leave day or their
    branch's weekly-off day is deferred — it's included on the run whose
    calendar day is that assignee's next actual working day, not on the
    literal due date.
    """
    from datetime import date as _date
    from .database import SessionLocal, Tenant, ChecklistAssignment, Notification
    from .notifications import create_notification, claim_dedup_key
    from .notification_rules import channel_enabled, next_working_day_for, get_recipient_roles
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        current_hour = now.hour
        today = _date.today()
        today_start = datetime.combine(today, datetime.min.time())
        today_end   = datetime.combine(today, datetime.max.time())
        # Small lookback window so an item deferred off a recent leave/off day
        # still gets swept up once its assignee's next working day arrives,
        # without reaching back into the general overdue backlog.
        lookback_start = today_start - timedelta(days=3)

        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            notif_hours = _parse_notif_hours(getattr(tenant, "checklist_notif_hours", None))
            if current_hour not in notif_hours:
                continue

            # Dedup: already sent for this tenant+hour today? Atomic claim so
            # two scheduler instances racing on the same tick can't both send.
            window_start = now.replace(minute=0, second=0, microsecond=0)
            if not claim_dedup_key(db, f"checklist_reminder:{tenant.id}:{window_start.isoformat()}"):
                continue

            in_app_on = channel_enabled(db, tenant.id, "checklist_reminder", "in_app")
            push_on = channel_enabled(db, tenant.id, "checklist_reminder", "push")
            if not in_app_on and not push_on:
                continue
            if "ASSIGNEE" not in get_recipient_roles(db, tenant.id, "checklist_reminder"):
                continue  # this tenant has turned the reminder's only possible recipient off

            # Candidates: still-open (never OVERDUE — that's the backlog,
            # tracked by status only) assignments due within the lookback
            # window through end of today.
            candidates = db.query(ChecklistAssignment).filter(
                ChecklistAssignment.tenant_id == tenant.id,
                ChecklistAssignment.due_at >= lookback_start,
                ChecklistAssignment.due_at <= today_end,
                ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
                ChecklistAssignment.is_deleted == False,
            ).all()
            if not candidates:
                continue

            # Leave/branch-aware eligibility: only include an item if today
            # is the assignee's resolved reminder day.
            eligible = [
                a for a in candidates
                if a.user and next_working_day_for(db, a.user, a.due_at.date()) == today
            ]
            if not eligible:
                continue

            by_user: dict = {}
            for a in eligible:
                by_user.setdefault(a.user_id, []).append(a)

            if current_hour <= 10:
                time_label = "🌅 Morning"
            elif current_hour <= 15:
                time_label = "☀ Midday"
            else:
                time_label = "🌆 End-of-day"

            for uid, items in by_user.items():
                titles = [a.template.title for a in items if a.template]
                listed = titles[:5]
                body = f"You have {len(items)} checklist(s) due today:\n• " + "\n• ".join(listed)
                if len(titles) > 5:
                    body += f"\n… and {len(titles) - 5} more."

                create_notification(
                    db, tenant_id=tenant.id, user_id=uid,
                    notif_type="CHECKLIST_REMINDER",
                    title=f"{time_label} checklist reminder ({len(items)} due)",
                    body=body,
                    link="/checklists",
                    condition_key="checklist_reminder",
                )

        db.commit()
        logger.info("Consolidated checklist notifications processed for %d tenants", len(tenants))
    except Exception as e:
        logger.error("send_consolidated_checklist_notifications error: %s", e)
        db.rollback()
    finally:
        db.close()


def send_unacknowledged_ticket_notifications():
    """Pipeline 3A — omniflow_ticket_unacknowledged.
    Run every 30 minutes. At the configured end-of-day IST hour (shared
    checklist_overdue_hour field), sends one personalised WhatsApp per Admin
    and per direct Manager for every ticket that is OPEN, unacknowledged,
    and older than 2 hours. Deduped per ticket per recipient per day.
    """
    from datetime import date as _date
    from .database import SessionLocal, Tenant, Ticket, User, WhatsAppMessageLog
    from .notifications import _send_gupshup_wa
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        current_hour = now.hour
        today_start = datetime.combine(_date.today(), datetime.min.time())
        two_hours_ago = now - timedelta(hours=2)

        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            raw_hour = getattr(tenant, "checklist_overdue_hour", None)
            if not raw_hour:
                continue
            try:
                eod_utc = (int(raw_hour.strip()) - 5) % 24
            except (ValueError, AttributeError):
                continue
            if current_hour != eod_utc:
                continue

            unacked = db.query(Ticket).filter(
                Ticket.tenant_id == tenant.id,
                Ticket.status == "OPEN",
                Ticket.acknowledged_at == None,
                Ticket.created_at < two_hours_ago,
                Ticket.is_deleted == False,
            ).all()
            if not unacked:
                continue

            for ticket in unacked:
                admins = db.query(User).filter(
                    User.tenant_id == tenant.id,
                    User.role == "ADMIN",
                    User.is_deleted == False,
                    User.is_active == True,
                ).all()

                manager = None
                assignee_name = "the assignee"
                if ticket.current_assignee_id:
                    assignee = db.query(User).filter(User.id == ticket.current_assignee_id).first()
                    if assignee:
                        assignee_name = assignee.name
                        if assignee.manager_id:
                            manager = db.query(User).filter(User.id == assignee.manager_id).first()

                recipients = list(admins)
                if manager and manager not in recipients:
                    recipients.append(manager)

                hours_elapsed = str(int((now - ticket.created_at).total_seconds() / 3600))

                for recipient in recipients:
                    if not recipient.phone:
                        continue
                    already = db.query(WhatsAppMessageLog).filter(
                        WhatsAppMessageLog.template_name == "omniflow_ticket_unacknowledged",
                        WhatsAppMessageLog.recipient_user_id == recipient.id,
                        WhatsAppMessageLog.related_entity_id == ticket.id,
                        WhatsAppMessageLog.created_at >= today_start,
                    ).first()
                    if already:
                        continue

                    variables = [recipient.name, ticket.title, assignee_name, hours_elapsed]
                    _send_gupshup_wa(db, tenant.id, recipient, "omniflow_ticket_unacknowledged", variables,
                                      related_entity_type="ticket", related_entity_id=ticket.id)
            db.commit()
        logger.info("Unacknowledged ticket WhatsApp job complete")
    except Exception as e:
        logger.error("send_unacknowledged_ticket_notifications error: %s", e)
        db.rollback()
    finally:
        db.close()


def checklist_eod_overdue():
    """Mark past-due assignments OVERDUE at end of day (catch-all backup to mark_overdue_checklists)."""
    from .database import SessionLocal, ChecklistAssignment
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        past_due = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.due_at < today_start,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ChecklistAssignment.is_deleted == False,
        ).all()
        for a in past_due:
            a.status = "OVERDUE"
        db.commit()
        logger.info("EOD: marked %d assignments OVERDUE", len(past_due))
    except Exception as e:
        logger.error("checklist_eod_overdue error: %s", e)
        db.rollback()
    finally:
        db.close()


# ── Phase 3 jobs ─────────────────────────────────────────────────────────────

def pms_no_entry_check():
    """Phase 3-A-4: At 21:00 UTC log NO_ENTRY for FMS tickets without today's PMS log."""
    from datetime import date as _date
    from .database import SessionLocal, FMSTicket, FMSStage, PMSDailyLog, User, Notification
    from .notifications import create_notification
    db = SessionLocal()
    try:
        today = _date.today()
        now   = datetime.utcnow()
        if now.hour < 20:    # run only after 20:00 UTC
            return

        # Find all active FMS tickets at a PMS stage
        active = db.query(FMSTicket).join(
            FMSStage, FMSTicket.current_stage_id == FMSStage.id
        ).filter(
            FMSStage.sub_module_tag == "PMS",
            FMSTicket.status.notin_(FMS_INACTIVE_STATUSES),
            FMSTicket.is_deleted == False,
        ).all()

        for ticket in active:
            has_log = db.query(PMSDailyLog).filter(
                PMSDailyLog.ticket_id == ticket.id,
                PMSDailyLog.log_date  == today,
                PMSDailyLog.event_type == "DAILY_LOG",
            ).first()
            if not has_log:
                # Immutable no-entry event
                db.add(PMSDailyLog(
                    ticket_id=ticket.id, tenant_id=ticket.tenant_id,
                    log_date=today, qty_done=0,
                    event_type="NO_ENTRY", actor_id=None,
                ))
                # Notify managers
                managers = db.query(User).filter(
                    User.tenant_id == ticket.tenant_id,
                    User.role.in_(["ADMIN", "MANAGER"]),
                    User.is_deleted == False,
                ).all()
                for mgr in managers:
                    create_notification(
                        db, tenant_id=ticket.tenant_id, user_id=mgr.id,
                        notif_type="TICKET_STATUS_CHANGED",
                        title="⚠ No PMS entry today",
                        body=f'"{ticket.title}" has no production log entry for today.',
                        link=f"/fms/tickets/{ticket.id}",
                    )
        db.commit()
        logger.info("PMS no-entry check done for %d tickets", len(active))
    except Exception as e:
        logger.error("pms_no_entry_check error: %s", e)
        db.rollback()
    finally:
        db.close()


def dispatch_pod_overdue_check():
    """Phase 3-B-6: Alert if POD not uploaded N days after expected delivery."""
    from .database import SessionLocal, DispatchRecord, FMSTicket, User, Notification
    from .notifications import create_notification
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        overdue_window = now - timedelta(days=3)   # default 3-day threshold

        pending_pods = db.query(DispatchRecord).filter(
            DispatchRecord.expected_delivery != None,
            DispatchRecord.expected_delivery < overdue_window,
            DispatchRecord.is_delivered == False,
            DispatchRecord.proof_photo_url == None,
        ).all()

        for rec in pending_pods:
            ticket = db.query(FMSTicket).get(rec.ticket_id)
            if not ticket:
                continue
            # Dedup: only one alert per record per 24h
            dup = db.query(Notification).filter(
                Notification.notif_type == "TICKET_FLAGGED",
                Notification.link == f"/submodules/dispatch/{rec.ticket_id}",
                Notification.created_at > (now - timedelta(hours=24)),
            ).first()
            if not dup:
                recipients = db.query(User).filter(
                    User.tenant_id == rec.tenant_id,
                    User.role.in_(["ADMIN", "MANAGER"]),
                    User.is_deleted == False,
                ).all()
                for u in recipients:
                    create_notification(
                        db, tenant_id=rec.tenant_id, user_id=u.id,
                        notif_type="TICKET_FLAGGED",
                        title="⚠ POD overdue",
                        body=(f'Dispatch for "{ticket.title}" expected '
                              f'{rec.expected_delivery.strftime("%d %b")} — no POD yet.'),
                        link=f"/submodules/dispatch/{rec.ticket_id}",
                    )
        db.commit()
        logger.info("POD overdue check done, %d records flagged", len(pending_pods))
    except Exception as e:
        logger.error("dispatch_pod_overdue_check error: %s", e)
        db.rollback()
    finally:
        db.close()


def invoice_overdue_check():
    """Phase 3-C-5: Alert on overdue unpaid invoices."""
    from datetime import date as _date
    from .database import SessionLocal, InvoiceRecord, FMSTicket, User, Notification
    from .notifications import create_notification
    db = SessionLocal()
    try:
        today = _date.today()
        now   = datetime.utcnow()

        overdue_inv = db.query(InvoiceRecord).filter(
            InvoiceRecord.is_paid    == False,
            InvoiceRecord.is_deleted == False,
            InvoiceRecord.due_date   != None,
            InvoiceRecord.due_date   < today,
        ).all()

        for inv in overdue_inv:
            ticket = db.query(FMSTicket).get(inv.ticket_id)
            if not ticket:
                continue
            dup = db.query(Notification).filter(
                Notification.notif_type == "TICKET_FLAGGED",
                Notification.link == f"/submodules/invoice/{inv.ticket_id}",
                Notification.created_at > (now - timedelta(hours=24)),
            ).first()
            if not dup:
                recipients = db.query(User).filter(
                    User.tenant_id == inv.tenant_id,
                    User.role.in_(["ADMIN", "MANAGER"]),
                    User.is_deleted == False,
                ).all()
                for u in recipients:
                    create_notification(
                        db, tenant_id=inv.tenant_id, user_id=u.id,
                        notif_type="TICKET_FLAGGED",
                        title="⚠ Invoice overdue",
                        body=(f'Invoice {inv.invoice_number} for "{ticket.title}" '
                              f'was due {inv.due_date.strftime("%d %b")} — payment not received.'),
                        link=f"/submodules/invoice/{inv.ticket_id}",
                    )
        db.commit()
        logger.info("Invoice overdue check done, %d overdue", len(overdue_inv))
    except Exception as e:
        logger.error("invoice_overdue_check error: %s", e)
        db.rollback()
    finally:
        db.close()


# ── E-15: Delegation TaT + Unacknowledged monitors ───────────────────────────

def delegation_tat_monitor():
    """E-15: Every 30 min — notify manager at ticket_notif_tat_pct and ticket_notif_tat_pct_both of TaT elapsed."""
    from datetime import date as _date
    from .database import SessionLocal, Tenant, Ticket, TicketEvent, User, Notification
    from .notifications import business_hours_elapsed, send_whatsapp_for_ticket_tat_reminder, create_notification, claim_dedup_key
    from .notification_rules import channel_enabled, is_working_day_for
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today = _date.today()
        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            pct1 = getattr(tenant, 'ticket_notif_tat_pct', 80) or 0
            pct2 = getattr(tenant, 'ticket_notif_tat_pct_both', 90) or 0
            if pct1 == 0 and pct2 == 0:
                continue
            open_tickets = db.query(Ticket).filter(
                Ticket.tenant_id == tenant.id,
                Ticket.status == 'OPEN',
                Ticket.due_at != None,
                Ticket.is_deleted == False,
            ).all()
            for ticket in open_tickets:
                if not ticket.due_at or not ticket.created_at:
                    continue
                # Leave/branch-aware: defer the reminder while the assignee
                # is on approved leave or their branch's weekly-off — the TaT
                # clock itself keeps running, only the notification is held.
                _assignee_for_leave = db.query(User).filter(User.id == ticket.current_assignee_id).first() if ticket.current_assignee_id else None
                if _assignee_for_leave and not is_working_day_for(db, _assignee_for_leave, today):
                    continue
                total_biz = business_hours_elapsed(tenant, ticket.created_at, ticket.due_at)
                if total_biz <= 0:
                    continue
                elapsed_biz = business_hours_elapsed(tenant, ticket.created_at, now)
                pct_elapsed = (elapsed_biz / total_biz) * 100

                # Find managers/admins
                admins = db.query(User).filter(
                    User.tenant_id == tenant.id, User.role == 'ADMIN',
                    User.is_deleted == False, User.is_active == True,
                ).all()
                manager = None
                assignee = None
                if ticket.current_assignee_id:
                    assignee = db.query(User).filter(User.id == ticket.current_assignee_id).first()
                    if assignee and assignee.manager_id:
                        manager = db.query(User).filter(User.id == assignee.manager_id).first()
                assignee_name = assignee.name if assignee else "—"

                # pct1: manager + employee; pct2: manager + admin + employee.
                # This escalation ladder (fewer recipients at the earlier
                # threshold, admin added in at the later one) is intentional
                # and separate from the tenant's recipients config — the
                # config below only prunes roles the tenant doesn't want
                # notified at all, it doesn't collapse the two tiers together.
                from .notification_rules import filter_recipients
                mgr_id = manager.id if manager else None
                configured_audience = set(filter_recipients(
                    db, tenant.id, "ticket_tat_reminder",
                    admin_ids=[u.id for u in admins],
                    manager_ids=[mgr_id] if mgr_id else [],
                    assignee_id=ticket.current_assignee_id,
                ))
                for threshold_pct, audience_ids in [
                    (pct1, ([mgr_id] if mgr_id else []) + ([ticket.current_assignee_id] if ticket.current_assignee_id else [])),
                    (pct2, [u.id for u in admins] + ([mgr_id] if mgr_id else []) + ([ticket.current_assignee_id] if ticket.current_assignee_id else [])),
                ]:
                    audience_ids = [uid for uid in audience_ids if uid in configured_audience]
                    if threshold_pct == 0 or pct_elapsed < threshold_pct:
                        continue
                    # Dedup: already notified at this threshold? Exact bracketed
                    # tag avoids substring collisions (e.g. threshold 8 matching "80").
                    # Atomic claim so two scheduler instances racing on the same
                    # tick can't both pass this check and double-send.
                    tat_tag = f'[tat_alert:{threshold_pct}]'
                    if not claim_dedup_key(db, f"ticket_tat_reminder:{ticket.id}:{threshold_pct}"):
                        continue
                    # Log audit event. TicketEvent.actor_id is NOT NULL and this
                    # is a system-generated event — attribute it to the ticket
                    # creator (always set) rather than the assignee (nullable).
                    db.add(TicketEvent(
                        ticket_id=ticket.id,
                        actor_id=ticket.created_by_id,
                        event_type='TAT_ALERT',
                        detail=f'TaT alert at {threshold_pct}% threshold ({pct_elapsed:.0f}% elapsed) {tat_tag}',
                    ))
                    for uid in set(audience_ids):
                        if not uid:
                            continue
                        create_notification(
                            db, tenant_id=tenant.id, user_id=uid,
                            notif_type='TICKET_REMINDER',
                            title=f'⏱ TaT alert: {ticket.display_id or ticket.title}',
                            body=f'{pct_elapsed:.0f}% of allowed time used on this ticket.',
                            link=f'/tickets/{ticket.id}',
                            condition_key='ticket_tat_reminder',
                        )
                        if channel_enabled(db, tenant.id, 'ticket_tat_reminder', 'whatsapp'):
                            recipient = db.query(User).filter(User.id == uid).first()
                            if recipient:
                                send_whatsapp_for_ticket_tat_reminder(
                                    db, ticket, recipient, assignee_name, round(pct_elapsed))
            db.commit()
    except Exception as e:
        logger.error("delegation_tat_monitor error: %s", e)
        db.rollback()
    finally:
        db.close()


def fms_stage_tat_monitor():
    """E-15: Every 30 min — notify manager+admin+employee when FMS stage TaT
    exceeds fms_notif_tat_pct.

    Phase 0 (split flows): iterates active FMSTicketSplit rows rather than
    FMSTicket directly — two splits of the same ticket can breach TAT
    independently, at different stages, at different times, so a per-ticket
    check would only ever catch one of them."""
    from datetime import date as _date
    from .database import SessionLocal, Tenant, FMSTicket, FMSTicketSplit, FMSStage, FMSStageHistory, User, Notification
    from .notifications import business_hours_elapsed, create_notification, claim_dedup_key
    from .notification_rules import is_working_day_for
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today = _date.today()
        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            tat_pct = getattr(tenant, 'fms_notif_tat_pct', 80) or 0
            if tat_pct == 0:
                continue
            active_splits = db.query(FMSTicketSplit).join(
                FMSTicket, FMSTicketSplit.ticket_id == FMSTicket.id
            ).filter(
                FMSTicketSplit.tenant_id == tenant.id,
                FMSTicketSplit.is_deleted == False,
                FMSTicketSplit.status.notin_(FMS_INACTIVE_STATUSES),
                FMSTicket.is_deleted == False,
                FMSTicket.status != "CLOSED",
            ).all()
            for split in active_splits:
                stage = db.query(FMSStage).filter(FMSStage.id == split.current_stage_id).first()
                if not stage or not getattr(stage, 'target_tat_hours', None):
                    continue
                # Find when this split entered its current stage
                entry = db.query(FMSStageHistory).filter(
                    FMSStageHistory.split_id == split.id,
                    FMSStageHistory.stage_id == split.current_stage_id,
                ).order_by(FMSStageHistory.entered_at.desc()).first()
                if not entry:
                    continue
                elapsed = business_hours_elapsed(tenant, entry.entered_at, now)
                pct_used = (elapsed / stage.target_tat_hours) * 100
                if pct_used < tat_pct:
                    continue
                ticket = split.ticket
                is_multi = db.query(FMSTicketSplit).filter(
                    FMSTicketSplit.ticket_id == split.ticket_id,
                    FMSTicketSplit.is_deleted == False,
                ).count() > 1
                label_suffix = f" [{split.split_label}]" if is_multi else ""
                # Dedup — one alert per split's stage entry. Exact bracketed
                # tag (split id + entered_at) avoids substring collisions and
                # ensures a re-visit to the same stage (entered_at changes)
                # is treated as a fresh window, not a repeat.
                stage_entry_tag = f"[ref:{split.id}:{entry.entered_at.isoformat()}]"
                # Atomic claim so two scheduler instances racing on the same
                # tick can't both pass this check and double-send.
                if not claim_dedup_key(db, f"fms_tat_breach:{split.id}:{entry.entered_at.isoformat()}"):
                    continue
                # Audience: admins + manager + assignee
                admins = db.query(User).filter(
                    User.tenant_id == tenant.id, User.role == "ADMIN",
                    User.is_deleted == False, User.is_active == True,
                ).all()
                assignee = db.query(User).filter(User.id == split.current_assignee_id).first() if split.current_assignee_id else None
                # Leave/branch-aware: defer while the assignee is off today —
                # the TaT clock keeps running, only the notification is held.
                if assignee and not is_working_day_for(db, assignee, today):
                    continue
                manager = db.query(User).filter(User.id == assignee.manager_id).first() if assignee and assignee.manager_id else None
                from .notification_rules import filter_recipients
                audience = filter_recipients(
                    db, tenant.id, "fms_tat_breach",
                    admin_ids=[u.id for u in admins],
                    manager_ids=[manager.id] if manager else [],
                    assignee_id=assignee.id if assignee else None,
                )
                for uid in audience:
                    create_notification(
                        db, tenant_id=tenant.id, user_id=uid,
                        notif_type="TICKET_REMINDER",
                        title=f"⏱ Stage TaT alert: {ticket.title}{label_suffix}",
                        body=f"{pct_used:.0f}% of stage TaT used at '{stage.name}' stage. {stage_entry_tag}",
                        link=f"/fms/tickets/{ticket.id}",
                        condition_key="fms_tat_breach",
                    )
            db.commit()
    except Exception as e:
        logger.error("fms_stage_tat_monitor error: %s", e)
        db.rollback()
    finally:
        db.close()


def fms_qty_discrepancy_monitor():
    """Phase 0 (split flows brief §5): periodic re-check of every multi-split
    ticket's active-split qty sum against target_qty, catching drift that
    wasn't introduced by the split action itself (e.g. a manual qty edit
    elsewhere). Same cadence/registration pattern as fms_stage_tat_monitor."""
    from sqlalchemy import func
    from .database import SessionLocal, FMSTicket, FMSTicketSplit
    from .fms import _check_qty_discrepancy
    db = SessionLocal()
    try:
        multi_ticket_ids = [
            row[0] for row in db.query(FMSTicketSplit.ticket_id).filter(
                FMSTicketSplit.is_deleted == False
            ).group_by(FMSTicketSplit.ticket_id).having(func.count(FMSTicketSplit.id) > 1)
        ]
        if not multi_ticket_ids:
            return
        tickets = db.query(FMSTicket).filter(
            FMSTicket.id.in_(multi_ticket_ids),
            FMSTicket.is_deleted == False,
            FMSTicket.status.notin_(FMS_INACTIVE_STATUSES),
        ).all()
        for ticket in tickets:
            _check_qty_discrepancy(db, ticket)
        db.commit()
    except Exception as e:
        logger.error("fms_qty_discrepancy_monitor error: %s", e)
        db.rollback()
    finally:
        db.close()


def job_follow_up_reminders():
    """Brief 4: CRM follow-up reminders, daily 5 PM IST (11:30 UTC)."""
    from .database import SessionLocal
    from .sales_contacts import send_follow_up_reminders
    db = SessionLocal()
    try:
        send_follow_up_reminders(db)
    except Exception as e:
        logger.error("job_follow_up_reminders error: %s", e)
        db.rollback()
    finally:
        db.close()


def job_tier_classification():
    """Brief 7: weekly A/B/C/D tier classification for products and customers."""
    from .database import SessionLocal, Tenant
    from .constants import has_feature
    from .sales_ai import run_tier_classification
    db = SessionLocal()
    try:
        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            if has_feature(tenant, "SALES_ANALYTICS", db):
                run_tier_classification(db, tenant.id)
    except Exception as e:
        logger.error("job_tier_classification error: %s", e)
        db.rollback()
    finally:
        db.close()


def job_anomaly_detection():
    """Brief 7: daily AI-narrated anomaly detection for the Sales module."""
    import anthropic
    from .database import SessionLocal, Tenant
    from .constants import has_feature
    from .sales_ai import run_anomaly_detection
    client = anthropic.Anthropic()
    db = SessionLocal()
    try:
        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            if has_feature(tenant, "SALES_ANALYTICS", db):
                run_anomaly_detection(db, tenant.id, client)
    except Exception as e:
        logger.error("job_anomaly_detection error: %s", e)
        db.rollback()
    finally:
        db.close()


def job_release_expired_reservations():
    """Brief 5: every 2 hours — auto-release ACTIVE reservations past their expires_at."""
    from .database import SessionLocal, SalesOrder, StockReservation
    from .sales_inventory import release_all_reservations
    db = SessionLocal()
    try:
        expired_order_ids = (
            db.query(StockReservation.order_id.distinct())
            .filter(StockReservation.status    == "ACTIVE",
                    StockReservation.expires_at != None,
                    StockReservation.expires_at <  datetime.utcnow())
            .all()
        )
        for (order_id,) in expired_order_ids:
            tenant_id = db.query(SalesOrder.tenant_id).filter(
                SalesOrder.id == order_id
            ).scalar()
            if tenant_id:
                release_all_reservations(db, order_id, tenant_id, reason="Auto-expired after 24h")
        db.commit()
    except Exception as e:
        logger.error("job_release_expired_reservations error: %s", e)
        db.rollback()
    finally:
        db.close()


# ── Start / stop ──────────────────────────────────────────────────────────────

def start_scheduler():
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(generate_recurring_checklists,
                      IntervalTrigger(hours=1), id="gen_cl",    replace_existing=True)
    scheduler.add_job(mark_overdue_checklists,
                      IntervalTrigger(minutes=15), id="overdue", replace_existing=True)
    scheduler.add_job(escalate_unacknowledged_tickets,
                      IntervalTrigger(minutes=30), id="escalate",replace_existing=True)
    # Phase 5 jobs — 8:00 AM IST = 2:30 UTC (previously wrongly registered at
    # 8:00 UTC = 1:30 PM IST despite the "morning" name — fixed)
    scheduler.add_job(morning_ticket_summary,
                      CronTrigger(hour=2, minute=30, timezone="UTC"), id="morning_tickets", replace_existing=True)
    # The single checklist reminder job — merges the old "remind" (legacy
    # per-assignment) + "cl_consolidated" jobs into one: due-today only,
    # fires once at each tenant-configured hour, leave/branch-aware.
    scheduler.add_job(send_consolidated_checklist_notifications,
                      IntervalTrigger(minutes=30), id="cl_consolidated", replace_existing=True)
    scheduler.add_job(send_unacknowledged_ticket_notifications,
                      IntervalTrigger(minutes=30), id="ticket_unacked_wa", replace_existing=True)
    scheduler.add_job(checklist_eod_overdue,
                      CronTrigger(hour=18, minute=0, timezone="UTC"), id="cl_eod", replace_existing=True)
    # Phase 3 jobs
    scheduler.add_job(pms_no_entry_check,
                      IntervalTrigger(hours=1), id="pms_noe",   replace_existing=True)
    scheduler.add_job(dispatch_pod_overdue_check,
                      IntervalTrigger(hours=6), id="pod_chk",   replace_existing=True)
    scheduler.add_job(invoice_overdue_check,
                      IntervalTrigger(hours=6), id="inv_chk",   replace_existing=True)
    # E-15 jobs
    scheduler.add_job(delegation_tat_monitor,
                      IntervalTrigger(minutes=30), id="deleg_tat",    replace_existing=True)
    scheduler.add_job(fms_stage_tat_monitor,
                      IntervalTrigger(minutes=30), id="fms_tat",      replace_existing=True)
    # Phase 0 (split flows) — periodic qty discrepancy re-check, same cadence
    scheduler.add_job(fms_qty_discrepancy_monitor,
                      IntervalTrigger(minutes=30), id="fms_qty_discrepancy", replace_existing=True)
    # Brief 4 — CRM follow-up reminders, daily 5 PM IST
    scheduler.add_job(job_follow_up_reminders,
                      CronTrigger(hour=11, minute=30, timezone="UTC"), id="crm_followup", replace_existing=True)
    # Brief 5 — auto-release expired stock reservations
    scheduler.add_job(job_release_expired_reservations,
                      IntervalTrigger(hours=2), id="release_expired_reservations", replace_existing=True)
    # Brief 7 — tier classification: every Monday 2 AM IST (Sun 20:30 UTC)
    scheduler.add_job(job_tier_classification,
                      CronTrigger(day_of_week="sun", hour=20, minute=30, timezone="UTC"),
                      id="sales_tier_classification", replace_existing=True)
    # Brief 7 — anomaly detection: daily 6 AM IST (00:30 UTC)
    scheduler.add_job(job_anomaly_detection,
                      CronTrigger(hour=0, minute=30, timezone="UTC"),
                      id="sales_anomaly_detection", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    logger.info("Scheduler started (17 jobs)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
