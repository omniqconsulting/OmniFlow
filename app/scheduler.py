"""
Background scheduler — Phase 0-B-1, 0-B-2, 0-B-3/4/5, 0-D-5
"""
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

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
            ft = getattr(tmpl, "frequency_type", None)
            cfg = getattr(tmpl, "frequency_config", None) or {}
            if ft == "WEEKLY_CUSTOM":
                days = cfg.get("days", [])
                if now.weekday() not in days:
                    continue
                next_due = now.replace(hour=18, minute=0, second=0, microsecond=0)
                if now.hour >= 18:
                    next_due += timedelta(days=1)
            elif ft == "MONTHLY_DATE":
                target_day = cfg.get("day", 1)
                if now.day != target_day:
                    continue
                # Silently skip if month has fewer days (e.g. Feb 30)
                try:
                    next_due = now.replace(day=target_day, hour=18, minute=0, second=0, microsecond=0)
                except ValueError:
                    continue
            elif ft == "YEARLY_DATE":
                target_month = cfg.get("month", 1)
                target_day = cfg.get("day", 1)
                if now.month != target_month or now.day != target_day:
                    continue
                try:
                    next_due = now.replace(month=target_month, day=target_day, hour=18, minute=0, second=0, microsecond=0)
                except ValueError:
                    continue
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
                next_due = now.replace(hour=18, minute=0, second=0, microsecond=0)
                if now.hour >= 18:
                    next_due += timedelta(days=1)
            elif tmpl.frequency == "WEEKLY":
                lookback = timedelta(weeks=1)
                days_ahead = 7 - now.weekday()
                next_due = (now + timedelta(days=days_ahead)).replace(
                    hour=18, minute=0, second=0, microsecond=0)
            elif tmpl.frequency == "MONTHLY":
                lookback = timedelta(days=32)
                m = now.month % 12 + 1
                y = now.year + (1 if now.month == 12 else 0)
                next_due = now.replace(year=y, month=m, day=1,
                                       hour=18, minute=0, second=0, microsecond=0)
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


def send_checklist_reminders():
    """Legacy per-assignment reminder — kept for overdue repeat alerts only."""
    from .database import SessionLocal, ChecklistAssignment, Notification
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        # Overdue repeat every 4 hours (simple fallback — main notifications via consolidated job)
        overdue_list = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.status == "OVERDUE",
            ChecklistAssignment.is_deleted == False,
        ).all()
        for a in overdue_list:
            dup = db.query(Notification).filter(
                Notification.user_id == a.user_id,
                Notification.notif_type == "CHECKLIST_OVERDUE",
                Notification.created_at > (now - timedelta(hours=4)),
            ).first()
            if not dup:
                label = a.template.title if a.template else "Checklist"
                db.add(Notification(
                    tenant_id=a.tenant_id,
                    user_id=a.user_id,
                    notif_type="CHECKLIST_OVERDUE",
                    title="⚠ Overdue checklist",
                    body=f'"{label}" is overdue. Please complete it now.',
                    link="/checklists",
                ))
        db.commit()
    except Exception as e:
        logger.error("send_checklist_reminders error: %s", e)
        db.rollback()
    finally:
        db.close()


def escalate_unacknowledged_tickets():
    """Phase 0-D-5: alert admins/managers for tickets unacknowledged >2 hours."""
    from .database import SessionLocal, Ticket, Notification, User
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        threshold = now - timedelta(hours=2)
        unacked = db.query(Ticket).filter(
            Ticket.status == "OPEN",
            Ticket.created_at < threshold,
            Ticket.acknowledged_at == None,
            Ticket.is_deleted == False,
        ).all()

        for ticket in unacked:
            recipients = db.query(User).filter(
                User.tenant_id == ticket.tenant_id,
                User.role.in_(["ADMIN", "MANAGER"]),
                User.is_deleted == False,
                User.is_active == True,
            ).all()
            for u in recipients:
                dup = db.query(Notification).filter(
                    Notification.user_id == u.id,
                    Notification.notif_type == "TICKET_ESCALATION",
                    Notification.link == f"/tickets/{ticket.id}",
                    Notification.created_at > (now - timedelta(hours=2)),
                ).first()
                if not dup:
                    db.add(Notification(
                        tenant_id=ticket.tenant_id,
                        user_id=u.id,
                        notif_type="TICKET_ESCALATION",
                        title="⚠ Ticket not acknowledged",
                        body=f'"{ticket.title}" has been open for 2+ hours without acknowledgement.',
                        link=f"/tickets/{ticket.id}",
                    ))
        db.commit()
    except Exception as e:
        logger.error("escalate_unacknowledged_tickets error: %s", e)
        db.rollback()
    finally:
        db.close()


# ── Phase 5 jobs ─────────────────────────────────────────────────────────────

def morning_ticket_summary():
    """P5-05: 8:00 AM daily — notify each user of their open/in-progress tickets due today or overdue."""
    from datetime import date as _date
    from .database import SessionLocal, Ticket, User, Notification
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today_end = datetime.combine(_date.today(), datetime.max.time())

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
            overdue = [t for t in user_tickets if t.due_at and t.due_at < now]
            due_today = [t for t in user_tickets if t.due_at and t.due_at >= now]
            parts = []
            if overdue:
                parts.append(f"{len(overdue)} overdue")
            if due_today:
                parts.append(f"{len(due_today)} due today")
            if not parts:
                continue
            tenant_id = user_tickets[0].tenant_id
            db.add(Notification(
                tenant_id=tenant_id, user_id=uid,
                notif_type="TICKET_REMINDER",
                title="🌅 Morning ticket summary",
                body=f"You have {', '.join(parts)} ticket(s) needing attention.",
                link="/tickets?status=OPEN",
            ))
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
    """Run every 30 minutes. For each tenant, fire consolidated per-employee
    checklist notifications at their configured UTC hours (default 8, 13, 18).

    Each employee receives a single notification listing ALL their pending
    checklists due today — not one per checklist."""
    from datetime import date as _date
    from .database import SessionLocal, Tenant, ChecklistAssignment, Notification
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        current_hour = now.hour
        today_start = datetime.combine(_date.today(), datetime.min.time())
        today_end   = datetime.combine(_date.today(), datetime.max.time())

        # Fire if we're within the first 30 minutes of any configured hour
        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            notif_hours = _parse_notif_hours(getattr(tenant, "checklist_notif_hours", None))
            if current_hour not in notif_hours:
                continue

            # Dedup: already sent for this tenant+hour today?
            window_start = now.replace(minute=0, second=0, microsecond=0)
            already_sent = db.query(Notification).filter(
                Notification.tenant_id == tenant.id,
                Notification.notif_type == "CHECKLIST_REMINDER",
                Notification.created_at >= window_start,
            ).first()
            if already_sent:
                continue

            # All pending/in-progress assignments due today for this tenant
            pending = db.query(ChecklistAssignment).filter(
                ChecklistAssignment.tenant_id == tenant.id,
                ChecklistAssignment.due_at >= today_start,
                ChecklistAssignment.due_at <= today_end,
                ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS", "OVERDUE"]),
                ChecklistAssignment.is_deleted == False,
            ).all()

            if not pending:
                continue

            # Group by user — each employee gets their own notification
            by_user: dict = {}
            for a in pending:
                by_user.setdefault(a.user_id, []).append(a)

            # Choose title emoji based on hour
            if current_hour <= 10:
                time_label = "🌅 Morning"
            elif current_hour <= 15:
                time_label = "☀ Midday"
            else:
                time_label = "🌆 End-of-day"

            for uid, items in by_user.items():
                titles = [a.template.title for a in items if a.template]
                overdue_count = sum(1 for a in items if a.status == "OVERDUE")
                pending_count = len(items) - overdue_count

                parts = []
                if pending_count:
                    parts.append(f"{pending_count} pending")
                if overdue_count:
                    parts.append(f"{overdue_count} overdue")
                summary = ", ".join(parts)

                # Build body with the actual checklist titles (up to 5)
                listed = titles[:5]
                body = f"You have {summary} checklist(s) for today:\n• " + "\n• ".join(listed)
                if len(titles) > 5:
                    body += f"\n… and {len(titles) - 5} more."

                db.add(Notification(
                    tenant_id=tenant.id,
                    user_id=uid,
                    notif_type="CHECKLIST_REMINDER",
                    title=f"{time_label} checklist reminder ({len(items)} due)",
                    body=body,
                    link="/checklists",
                ))

                # Pipeline 2A: WhatsApp for PENDING + IN_PROGRESS only (OVERDUE excluded)
                wa_items = [a for a in items if a.status in ("PENDING", "IN_PROGRESS")]
                if wa_items:
                    _send_wa_checklist_due(db, tenant.id, uid, wa_items)

        db.commit()
        logger.info("Consolidated checklist notifications processed for %d tenants", len(tenants))
    except Exception as e:
        logger.error("send_consolidated_checklist_notifications error: %s", e)
        db.rollback()
    finally:
        db.close()


def _send_wa_checklist_due(db, tenant_id: str, user_id: str, assignments: list):
    """Pipeline 2A — omniflow_checklist_due. Never raises."""
    from .database import User
    from .notifications import _send_gupshup_wa
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.phone:
            return
        titles_csv = _build_checklist_titles_csv(assignments)
        if not titles_csv:
            return
        variables = [user.name, titles_csv]
        _send_gupshup_wa(db, tenant_id, user, "omniflow_checklist_due", variables,
                          related_entity_type="checklist_reminder", related_entity_id=user_id)
    except Exception:
        logger.exception("_send_wa_checklist_due failed for user=%s", user_id)


def _send_wa_checklist_overdue(db, tenant_id: str, user_id: str, assignments: list):
    """Pipeline 2B — omniflow_checklist_overdue. Never raises."""
    from .database import User
    from .notifications import _send_gupshup_wa
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.phone:
            return
        titles_csv = _build_checklist_titles_csv(assignments)
        if not titles_csv:
            return
        variables = [user.name, titles_csv]
        _send_gupshup_wa(db, tenant_id, user, "omniflow_checklist_overdue", variables,
                          related_entity_type="checklist_overdue_reminder", related_entity_id=user_id)
    except Exception:
        logger.exception("_send_wa_checklist_overdue failed for user=%s", user_id)


def send_consolidated_checklist_overdue_notifications():
    """Pipeline 2B — omniflow_checklist_overdue.
    Run every 30 minutes. At the single configured IST overdue hour, sends one
    WhatsApp per employee listing all their today assignments that are not DONE
    and not FAILED. Deduped per tenant per day. Fires only if
    tenant.checklist_overdue_hour is set.
    """
    from datetime import date as _date
    from .database import SessionLocal, Tenant, ChecklistAssignment, WhatsAppMessageLog
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        current_hour = now.hour
        today_start = datetime.combine(_date.today(), datetime.min.time())
        today_end   = datetime.combine(_date.today(), datetime.max.time())

        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            raw_overdue = getattr(tenant, "checklist_overdue_hour", None)
            if not raw_overdue:
                continue
            try:
                overdue_utc = (int(raw_overdue.strip()) - 5) % 24
            except (ValueError, AttributeError):
                continue
            if current_hour != overdue_utc:
                continue

            # Dedup: already sent overdue WhatsApp for this tenant today?
            already_sent = db.query(WhatsAppMessageLog).filter(
                WhatsAppMessageLog.tenant_id == tenant.id,
                WhatsAppMessageLog.template_name == "omniflow_checklist_overdue",
                WhatsAppMessageLog.created_at >= today_start,
            ).first()
            if already_sent:
                continue

            # Today's assignments that are not DONE and not FAILED
            incomplete = db.query(ChecklistAssignment).filter(
                ChecklistAssignment.tenant_id == tenant.id,
                ChecklistAssignment.due_at >= today_start,
                ChecklistAssignment.due_at <= today_end,
                ChecklistAssignment.status.notin_(["DONE", "FAILED"]),
                ChecklistAssignment.is_deleted == False,
            ).all()
            if not incomplete:
                continue

            by_user: dict = {}
            for a in incomplete:
                by_user.setdefault(a.user_id, []).append(a)

            for uid, items in by_user.items():
                _send_wa_checklist_overdue(db, tenant.id, uid, items)

        db.commit()
        logger.info("Checklist overdue WhatsApp processed for %d tenants", len(tenants))
    except Exception as e:
        logger.error("send_consolidated_checklist_overdue_notifications error: %s", e)
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
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
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
                    db.add(Notification(
                        tenant_id=ticket.tenant_id, user_id=mgr.id,
                        notif_type="TICKET_STATUS_CHANGED",
                        title="⚠ No PMS entry today",
                        body=f'"{ticket.title}" has no production log entry for today.',
                        link=f"/fms/tickets/{ticket.id}",
                    ))
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
                    db.add(Notification(
                        tenant_id=rec.tenant_id, user_id=u.id,
                        notif_type="TICKET_FLAGGED",
                        title="⚠ POD overdue",
                        body=(f'Dispatch for "{ticket.title}" expected '
                              f'{rec.expected_delivery.strftime("%d %b")} — no POD yet.'),
                        link=f"/submodules/dispatch/{rec.ticket_id}",
                    ))
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
                    db.add(Notification(
                        tenant_id=inv.tenant_id, user_id=u.id,
                        notif_type="TICKET_FLAGGED",
                        title="⚠ Invoice overdue",
                        body=(f'Invoice {inv.invoice_number} for "{ticket.title}" '
                              f'was due {inv.due_date.strftime("%d %b")} — payment not received.'),
                        link=f"/submodules/invoice/{inv.ticket_id}",
                    ))
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
    from .database import SessionLocal, Tenant, Ticket, TicketEvent, User, Notification
    from .notifications import business_hours_elapsed, send_whatsapp_for_ticket_tat_reminder
    db = SessionLocal()
    try:
        now = datetime.utcnow()
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

                # pct1: manager + employee; pct2: manager + admin + employee
                mgr_id = manager.id if manager else None
                for threshold_pct, audience_ids in [
                    (pct1, ([mgr_id] if mgr_id else []) + ([ticket.current_assignee_id] if ticket.current_assignee_id else [])),
                    (pct2, [u.id for u in admins] + ([mgr_id] if mgr_id else []) + ([ticket.current_assignee_id] if ticket.current_assignee_id else [])),
                ]:
                    if threshold_pct == 0 or pct_elapsed < threshold_pct:
                        continue
                    # Dedup: already notified at this threshold?
                    already = db.query(TicketEvent).filter(
                        TicketEvent.ticket_id == ticket.id,
                        TicketEvent.event_type == 'TAT_ALERT',
                        TicketEvent.notes.like(f'%{threshold_pct}%'),
                    ).first()
                    if already:
                        continue
                    # Log audit event
                    db.add(TicketEvent(
                        ticket_id=ticket.id,
                        event_type='TAT_ALERT',
                        notes=f'TaT alert at {threshold_pct}% threshold ({pct_elapsed:.0f}% elapsed)',
                    ))
                    for uid in set(audience_ids):
                        if not uid:
                            continue
                        db.add(Notification(
                            tenant_id=tenant.id, user_id=uid,
                            notif_type='TICKET_REMINDER',
                            title=f'⏱ TaT alert: {ticket.display_id or ticket.title}',
                            body=f'{pct_elapsed:.0f}% of allowed time used on this ticket.',
                            link=f'/tickets/{ticket.id}',
                        ))
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
    from .database import SessionLocal, Tenant, FMSTicket, FMSTicketSplit, FMSStage, FMSStageHistory, User, Notification
    from .notifications import business_hours_elapsed
    db = SessionLocal()
    try:
        now = datetime.utcnow()
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
                FMSTicketSplit.status.notin_(["COMPLETED", "CLOSED"]),
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
                # Dedup — one alert per split's stage entry
                already = db.query(Notification).filter(
                    Notification.notif_type == "TICKET_REMINDER",
                    Notification.link == f"/fms/tickets/{ticket.id}",
                    Notification.body.like(f"%stage TaT%{split.id}%"),
                    Notification.created_at > entry.entered_at,
                ).first()
                if already:
                    continue
                # Audience: admins + manager + assignee
                admins = db.query(User).filter(
                    User.tenant_id == tenant.id, User.role == "ADMIN",
                    User.is_deleted == False, User.is_active == True,
                ).all()
                assignee = db.query(User).filter(User.id == split.current_assignee_id).first() if split.current_assignee_id else None
                manager = db.query(User).filter(User.id == assignee.manager_id).first() if assignee and assignee.manager_id else None
                audience = list({u.id for u in admins} | ({manager.id} if manager else set()) | ({assignee.id} if assignee else set()))
                for uid in audience:
                    db.add(Notification(
                        tenant_id=tenant.id, user_id=uid,
                        notif_type="TICKET_REMINDER",
                        title=f"⏱ Stage TaT alert: {ticket.title}{label_suffix}",
                        body=f"{pct_used:.0f}% of stage TaT used at '{stage.name}' stage. [ref:{split.id}]",
                        link=f"/fms/tickets/{ticket.id}",
                    ))
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
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        ).all()
        for ticket in tickets:
            _check_qty_discrepancy(db, ticket)
        db.commit()
    except Exception as e:
        logger.error("fms_qty_discrepancy_monitor error: %s", e)
        db.rollback()
    finally:
        db.close()


def delegation_unacknowledged_monitor():
    """E-15: Every 30 min — notify manager if ticket OPEN with no activity for ticket_notif_unack_hours business hours."""
    from .database import SessionLocal, Tenant, Ticket, TicketEvent, User, Notification
    from .notifications import business_hours_elapsed
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        tenants = db.query(Tenant).filter(Tenant.is_suspended == False).all()
        for tenant in tenants:
            unack_hours = getattr(tenant, 'ticket_notif_unack_hours', 4) or 0
            if unack_hours == 0:
                continue
            open_tickets = db.query(Ticket).filter(
                Ticket.tenant_id == tenant.id,
                Ticket.status == 'OPEN',
                Ticket.is_deleted == False,
            ).all()
            for ticket in open_tickets:
                # Any event logged since creation?
                any_event = db.query(TicketEvent).filter(
                    TicketEvent.ticket_id == ticket.id,
                ).first()
                if any_event:
                    continue
                biz_elapsed = business_hours_elapsed(tenant, ticket.created_at, now)
                if biz_elapsed < unack_hours:
                    continue
                # Find manager
                manager = None
                if ticket.current_assignee_id:
                    assignee = db.query(User).filter(User.id == ticket.current_assignee_id).first()
                    if assignee and assignee.manager_id:
                        manager = db.query(User).filter(User.id == assignee.manager_id).first()
                admins = db.query(User).filter(
                    User.tenant_id == tenant.id, User.role == 'ADMIN',
                    User.is_deleted == False, User.is_active == True,
                ).all()
                recipients = list({u.id for u in admins} | ({manager.id} if manager else set()))
                for uid in recipients:
                    dup = db.query(Notification).filter(
                        Notification.user_id == uid,
                        Notification.notif_type == 'TICKET_ESCALATION',
                        Notification.link == f'/tickets/{ticket.id}',
                        Notification.created_at > (now - timedelta(hours=unack_hours)),
                    ).first()
                    if not dup:
                        db.add(Notification(
                            tenant_id=tenant.id, user_id=uid,
                            notif_type='TICKET_ESCALATION',
                            title=f'⚠ No activity on {ticket.display_id or ticket.title}',
                            body=f'Ticket has had no activity for {unack_hours}+ business hours.',
                            link=f'/tickets/{ticket.id}',
                        ))
            db.commit()
    except Exception as e:
        logger.error("delegation_unacknowledged_monitor error: %s", e)
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
    scheduler.add_job(send_checklist_reminders,
                      IntervalTrigger(minutes=15), id="remind",  replace_existing=True)
    scheduler.add_job(escalate_unacknowledged_tickets,
                      IntervalTrigger(minutes=30), id="escalate",replace_existing=True)
    # Phase 5 jobs
    scheduler.add_job(morning_ticket_summary,
                      CronTrigger(hour=8, minute=0, timezone="UTC"), id="morning_tickets", replace_existing=True)
    # Phase 6 jobs — consolidated per-employee, per-tenant notifications every 30 min
    scheduler.add_job(send_consolidated_checklist_notifications,
                      IntervalTrigger(minutes=30), id="cl_consolidated", replace_existing=True)
    scheduler.add_job(send_consolidated_checklist_overdue_notifications,
                      IntervalTrigger(minutes=30), id="cl_overdue", replace_existing=True)
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
