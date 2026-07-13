"""
app/services/appeal_package.py
'Generate Appeal Package' export — Section 8.6 of the Gupshup migration brief.

Ships as a CSV/zip bundle rather than PDF (no PDF library in the stack today —
flagged explicitly, per the brief's own fallback allowance in 8.6). Produces
a cover summary + full consent events + full outbound message log for one
tenant and date range, all in a single downloadable zip.
"""
import csv
import io
import json
import zipfile
from datetime import datetime


def build_appeal_package(db, tenant, start_dt, end_dt) -> bytes:
    from ..database import WhatsAppConsentEvent, WhatsAppMessageLog, User

    events = db.query(WhatsAppConsentEvent).filter(
        WhatsAppConsentEvent.tenant_id == tenant.id,
        WhatsAppConsentEvent.created_at >= start_dt,
        WhatsAppConsentEvent.created_at <= end_dt,
    ).order_by(WhatsAppConsentEvent.created_at).all()

    messages = db.query(WhatsAppMessageLog).filter(
        WhatsAppMessageLog.tenant_id == tenant.id,
        WhatsAppMessageLog.created_at >= start_dt,
        WhatsAppMessageLog.created_at <= end_dt,
    ).order_by(WhatsAppMessageLog.created_at).all()

    opted_in_employees = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.whatsapp_opt_in_status.in_(["OPTED_IN", "MANUALLY_VERIFIED"]),
        User.is_deleted == False,
    ).count()
    opt_outs = sum(1 for e in events if e.event_type == "OPT_OUT_RECEIVED")
    unresolved_mismatches = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.whatsapp_opt_in_status == "MISMATCH",
        User.is_deleted == False,
    ).count()

    by_category = {}
    for m in messages:
        cat = m.template_category or "UNSPECIFIED"
        by_category[cat] = by_category.get(cat, 0) + 1

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Cover summary
        cover = io.StringIO()
        cover.write(f"OmniFlow WhatsApp Compliance Appeal Package\n")
        cover.write(f"Tenant: {tenant.name}\n")
        cover.write(f"Date range: {start_dt.date().isoformat()} to {end_dt.date().isoformat()}\n")
        cover.write(f"Generated: {datetime.utcnow().isoformat()}Z\n\n")
        cover.write(f"Total opted-in employees: {opted_in_employees}\n")
        cover.write(f"Total opt-outs in period: {opt_outs}\n")
        cover.write(f"Unresolved mismatches: {unresolved_mismatches}\n")
        for cat, count in by_category.items():
            cover.write(f"Messages sent ({cat}): {count}\n")
        cover.write(
            f"\n{opted_in_employees} opted-in recipients, {opt_outs} opt-outs, "
            f"{unresolved_mismatches} unresolved mismatches in the selected period.\n"
        )
        zf.writestr("cover_summary.txt", cover.getvalue())

        # Consent events CSV
        ev_buf = io.StringIO()
        writer = csv.writer(ev_buf)
        writer.writerow(["timestamp", "employee_id", "event_type", "phone_number",
                          "source", "actor_id", "gupshup_message_id", "notes", "raw_payload"])
        for e in events:
            writer.writerow([
                e.created_at.isoformat() if e.created_at else "",
                e.employee_id or "unmatched",
                e.event_type, e.phone_number, e.source or "",
                e.actor_id or "", e.gupshup_message_id or "",
                e.notes or "", json.dumps(e.raw_webhook_payload) if e.raw_webhook_payload else "",
            ])
        zf.writestr("consent_events.csv", ev_buf.getvalue())

        # Outbound message log CSV
        msg_buf = io.StringIO()
        writer = csv.writer(msg_buf)
        writer.writerow(["timestamp", "recipient_user_id", "recipient_phone", "template_name",
                          "template_category", "status", "delivery_status_history"])
        for m in messages:
            writer.writerow([
                m.created_at.isoformat() if m.created_at else "",
                m.recipient_user_id or "", m.recipient_phone, m.template_name,
                m.template_category or "", m.status,
                json.dumps(m.delivery_status_history) if m.delivery_status_history else "[]",
            ])
        zf.writestr("outbound_message_log.csv", msg_buf.getvalue())

    return buf.getvalue()
