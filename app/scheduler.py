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
            # Determine lookback window and next due time by frequency
            if tmpl.frequency == "DAILY":
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
                # Skip if an assignment already exists in this window
                existing = db.query(ChecklistAssignment).filter(
                    ChecklistAssignment.template_id == tmpl.id,
                    ChecklistAssignment.user_id == u.id,
                    ChecklistAssignment.due_at > (now - lookback),
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
    """Phase 0-B-2: mark pending/in-progress assignments as OVERDUE every 15 min."""
    from .database import SessionLocal, ChecklistAssignment
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        overdue = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.due_at < now,
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
    """Phase 0-B-3/4/5: send reminder notifications before/at/after due time."""
    from .database import SessionLocal, ChecklistAssignment, Notification
    db = SessionLocal()
    try:
        now = datetime.utcnow()

        # 2h-before and at-due reminders
        look_ahead = now + timedelta(hours=2, minutes=30)
        upcoming = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.due_at > now,
            ChecklistAssignment.due_at <= look_ahead,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
        ).all()

        for a in upcoming:
            hours_before = (a.template.reminder_hours_before
                            if a.template else 2) or 2
            notify_at = a.due_at - timedelta(hours=hours_before)
            if now < notify_at:
                continue
            # Deduplicate: one reminder per assignment per 2.5h window
            dup = db.query(Notification).filter(
                Notification.user_id == a.user_id,
                Notification.notif_type == "CHECKLIST_REMINDER",
                Notification.link == "/checklists",
                Notification.created_at > (now - timedelta(hours=2, minutes=30)),
            ).first()
            if not dup:
                mins = max(int((a.due_at - now).total_seconds() / 60), 0)
                label = a.template.title if a.template else "Checklist"
                db.add(Notification(
                    tenant_id=a.tenant_id,
                    user_id=a.user_id,
                    notif_type="CHECKLIST_REMINDER",
                    title=f"Checklist due in {mins} min",
                    body=f'"{label}" is due soon.',
                    link="/checklists",
                ))

        # Overdue repeat every N hours
        overdue_list = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.status == "OVERDUE",
        ).all()
        for a in overdue_list:
            repeat_h = (a.template.reminder_repeat_hours
                        if a.template else 4) or 4
            dup = db.query(Notification).filter(
                Notification.user_id == a.user_id,
                Notification.notif_type == "CHECKLIST_OVERDUE",
                Notification.created_at > (now - timedelta(hours=repeat_h)),
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

def checklist_morning_summary():
    """P6-02: 8:00 AM — notify each user of PENDING/IN_PROGRESS assignments due today."""
    from datetime import date as _date
    from .database import SessionLocal, ChecklistAssignment, Notification
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today_start = datetime.combine(_date.today(), datetime.min.time())
        today_end   = datetime.combine(_date.today(), datetime.max.time())
        pending = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.due_at >= today_start,
            ChecklistAssignment.due_at <= today_end,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ChecklistAssignment.is_deleted == False,
        ).all()
        by_user: dict = {}
        for a in pending:
            by_user.setdefault((a.user_id, a.tenant_id), []).append(a)
        for (uid, tid), items in by_user.items():
            label = items[0].template.title if items[0].template else "a checklist"
            body = f"You have {len(items)} checklist(s) due today. First: \"{label}\"."
            db.add(Notification(tenant_id=tid, user_id=uid,
                                notif_type="CHECKLIST_REMINDER",
                                title="🌅 Morning checklist summary",
                                body=body, link="/checklists"))
        db.commit()
        logger.info("Checklist morning summary sent to %d users", len(by_user))
    except Exception as e:
        logger.error("checklist_morning_summary error: %s", e)
        db.rollback()
    finally:
        db.close()


def checklist_midday_reminder():
    """P6-02: 1:00 PM — remind users of still-incomplete assignments due today."""
    from datetime import date as _date
    from .database import SessionLocal, ChecklistAssignment, Notification
    db = SessionLocal()
    try:
        today_end = datetime.combine(_date.today(), datetime.max.time())
        now = datetime.utcnow()
        pending = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.due_at <= today_end,
            ChecklistAssignment.due_at >= now,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ChecklistAssignment.is_deleted == False,
        ).all()
        by_user: dict = {}
        for a in pending:
            by_user.setdefault((a.user_id, a.tenant_id), []).append(a)
        for (uid, tid), items in by_user.items():
            db.add(Notification(tenant_id=tid, user_id=uid,
                                notif_type="CHECKLIST_REMINDER",
                                title="☀ Midday reminder",
                                body=f"You still have {len(items)} pending checklist(s) due today.",
                                link="/checklists"))
        db.commit()
        logger.info("Checklist midday reminder sent to %d users", len(by_user))
    except Exception as e:
        logger.error("checklist_midday_reminder error: %s", e)
        db.rollback()
    finally:
        db.close()


def checklist_eod_overdue():
    """P6-02: 6:00 PM — mark past-due assignments OVERDUE then notify assignees."""
    from .database import SessionLocal, ChecklistAssignment, Notification
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        past_due = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.due_at < now,
            ChecklistAssignment.status.in_(["PENDING", "IN_PROGRESS"]),
            ChecklistAssignment.is_deleted == False,
        ).all()
        for a in past_due:
            a.status = "OVERDUE"
        db.flush()
        by_user: dict = {}
        for a in past_due:
            by_user.setdefault((a.user_id, a.tenant_id), []).append(a)
        for (uid, tid), items in by_user.items():
            db.add(Notification(tenant_id=tid, user_id=uid,
                                notif_type="CHECKLIST_OVERDUE",
                                title="🌆 End-of-day: overdue checklists",
                                body=f"{len(items)} checklist(s) are now marked OVERDUE. Please complete with a delay reason.",
                                link="/checklists"))
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
    # Phase 6 jobs
    scheduler.add_job(checklist_morning_summary,
                      CronTrigger(hour=8, minute=0, timezone="UTC"), id="cl_morning", replace_existing=True)
    scheduler.add_job(checklist_midday_reminder,
                      CronTrigger(hour=13, minute=0, timezone="UTC"), id="cl_midday", replace_existing=True)
    scheduler.add_job(checklist_eod_overdue,
                      CronTrigger(hour=18, minute=0, timezone="UTC"), id="cl_eod", replace_existing=True)
    # Phase 3 jobs
    scheduler.add_job(pms_no_entry_check,
                      IntervalTrigger(hours=1), id="pms_noe",   replace_existing=True)
    scheduler.add_job(dispatch_pod_overdue_check,
                      IntervalTrigger(hours=6), id="pod_chk",   replace_existing=True)
    scheduler.add_job(invoice_overdue_check,
                      IntervalTrigger(hours=6), id="inv_chk",   replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    logger.info("Scheduler started (11 jobs)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
