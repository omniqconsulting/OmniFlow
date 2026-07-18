"""
One-time backfill: recompute FMS stage-schedule planned dates for existing
open tickets/stage-visits using office-hours-aware TaT math instead of raw
wall-clock, so a ticket opened outside working hours doesn't show a TaT
window that started ticking overnight/over the weekend.

Scope, deliberately narrow:
  - FMSTicket.stage_schedule_json is recomputed for every non-deleted,
    non-COMPLETED/CLOSED ticket.
  - The *currently open* FMSStageHistory row (exited_at is None) for each
    such ticket has its planned_start/planned_end corrected to match.
  - Already-closed FMSStageHistory rows are left untouched — they are an
    immutable audit log (see the model's docstring) and, per the July 2026
    performance-scoring change, on-time/late is now judged on actual net
    hours spent vs target_tat_hours directly, not on planned_end — so a
    closed row's old planned_end no longer affects anyone's performance
    score. Recomputing it would only rewrite history for no behavioural
    benefit.

Usage:
    python backfill_office_hours_tat.py            # takes a DB backup, then runs
    python backfill_office_hours_tat.py --dry-run   # report only, no writes, no backup
"""
import sys
import shutil
from datetime import datetime

sys.path.insert(0, ".")

DB_FILE = "omniflow.db"


def main():
    dry_run = "--dry-run" in sys.argv

    if not dry_run:
        backup_name = f"{DB_FILE}.bak-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(DB_FILE, backup_name)
        print(f"Backed up {DB_FILE} -> {backup_name}")

    from app.database import SessionLocal, Tenant, FMSTicket, FMSStageHistory
    from app.fms import _planned_dates
    import json

    db = SessionLocal()
    tickets_updated = 0
    history_rows_updated = 0
    tenants_seen = {}

    try:
        tickets = db.query(FMSTicket).filter(
            FMSTicket.is_deleted == False,
            FMSTicket.status.notin_(["COMPLETED", "CLOSED"]),
        ).all()

        for ticket in tickets:
            if not ticket.flow:
                continue
            tenant = tenants_seen.get(ticket.tenant_id)
            if tenant is None:
                tenant = db.query(Tenant).get(ticket.tenant_id)
                tenants_seen[ticket.tenant_id] = tenant
            if tenant is None:
                continue

            stages = [s for s in ticket.flow.stages if not s.is_deleted]
            if not stages:
                continue

            new_schedule = _planned_dates(ticket, stages, tenant)
            new_schedule_json = json.dumps({
                sid: {"planned_start": ps.isoformat(), "planned_end": pe.isoformat()}
                for sid, (ps, pe) in new_schedule.items()
            })

            if new_schedule_json != (ticket.stage_schedule_json or ""):
                print(f"  ticket {ticket.display_id or ticket.id}: stage_schedule_json updated")
                if not dry_run:
                    ticket.stage_schedule_json = new_schedule_json
                tickets_updated += 1

            open_row = db.query(FMSStageHistory).filter(
                FMSStageHistory.ticket_id == ticket.id,
                FMSStageHistory.exited_at == None,
            ).order_by(FMSStageHistory.entered_at.desc()).first()

            if open_row and open_row.stage_id in new_schedule:
                ps, pe = new_schedule[open_row.stage_id]
                if open_row.planned_start != ps or open_row.planned_end != pe:
                    print(f"    open stage-history row {open_row.id}: "
                          f"planned_end {open_row.planned_end} -> {pe}")
                    if not dry_run:
                        open_row.planned_start = ps
                        open_row.planned_end = pe
                    history_rows_updated += 1

        if not dry_run:
            db.commit()
            print(f"\nDone. {tickets_updated} ticket schedule(s), "
                  f"{history_rows_updated} open stage-history row(s) updated.")
        else:
            print(f"\nDry run — would update {tickets_updated} ticket schedule(s), "
                  f"{history_rows_updated} open stage-history row(s). No changes written.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
