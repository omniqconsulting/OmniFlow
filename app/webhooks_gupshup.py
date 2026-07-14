"""
app/webhooks_gupshup.py
Inbound Gupshup webhook — Section 6 of the Gupshup migration brief.

One route per tenant, keyed by an opaque webhook_token (not the raw tenant_id)
so future tenant onboarding stays a Super Admin Portal data-entry task rather
than a code change (Section 6.0 "why a dedicated route per tenant").
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Request, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .database import get_db, Tenant, User, WhatsAppConsentEvent, WhatsAppMessageLog
from .services.gupshup import normalize_mobile
from fastapi import Depends

logger = logging.getLogger("webhooks_gupshup")

router = APIRouter()

_OPTED_IN_STATUSES = ("OPTED_IN", "MANUALLY_VERIFIED")


@router.get("/webhooks/gupshup/{webhook_token}")
async def gupshup_webhook_verify(webhook_token: str, db: Session = Depends(get_db)):
    """
    GET verification handshake — some webhook registration flows (Gupshup's
    console included, per live testing) probe with a GET before accepting the
    URL, independent of POST delivery working. Return 200 for any known
    token so registration doesn't fail with 'Invalid URL'.
    """
    tenant = db.query(Tenant).filter(Tenant.gupshup_webhook_token == webhook_token).first()
    if not tenant:
        return JSONResponse(status_code=404, content={"detail": "not found"})
    return JSONResponse(status_code=200, content={"detail": "ok"})


@router.post("/webhooks/gupshup/{webhook_token}")
async def gupshup_webhook(webhook_token: str, request: Request,
                           x_omniflow_webhook_secret: str | None = Header(default=None),
                           db: Session = Depends(get_db)):
    """
    Section 6.0: token lookup -> secret check -> parse -> route by type.
    Non-blocking (6.4): any processing failure is logged and swallowed —
    this endpoint must never 500 or block Gupshup's retry mechanism.
    """
    tenant = db.query(Tenant).filter(Tenant.gupshup_webhook_token == webhook_token).first()
    if not tenant:
        logger.warning("Gupshup webhook: unknown token %s", webhook_token)
        return JSONResponse(status_code=404, content={"detail": "not found"})

    if not tenant.gupshup_webhook_secret or not x_omniflow_webhook_secret or not hmac.compare_digest(
        x_omniflow_webhook_secret, tenant.gupshup_webhook_secret
    ):
        logger.warning("Gupshup webhook: secret mismatch for tenant %s", tenant.id)
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})

    try:
        body = await request.json()
    except Exception:
        logger.warning("Gupshup webhook: malformed JSON body for tenant %s", tenant.id)
        return JSONResponse(status_code=200, content={"detail": "ignored — malformed body"})

    try:
        _route_payload(db, tenant, body)
    except Exception:
        db.rollback()
        logger.exception("Gupshup webhook processing failed for tenant %s", tenant.id)

    return JSONResponse(status_code=200, content={"detail": "ok"})


def _route_payload(db: Session, tenant: Tenant, body: dict):
    """Gupshup format v2: {app, timestamp, version, type, payload}."""
    event_type = (body or {}).get("type", "")
    payload = (body or {}).get("payload", {}) or {}

    if event_type == "message":
        _handle_inbound_message(db, tenant, body, payload)
    elif event_type in ("user-event",) and payload.get("type") in ("opted-out", "sandbox-opt-out"):
        _handle_opt_out(db, tenant, body, payload)
    elif event_type == "message-event":
        _handle_status_event(db, tenant, payload)
    else:
        logger.info("Gupshup webhook: unhandled type=%s for tenant %s", event_type, tenant.id)


def _handle_inbound_message(db: Session, tenant: Tenant, body: dict, payload: dict):
    """Section 6.1 — opt-in matching."""
    sender_raw = payload.get("sender", {}).get("phone") or body.get("sender") or ""
    if not sender_raw:
        return
    sender_phone = normalize_mobile(str(sender_raw))
    gupshup_message_id = payload.get("id")

    employee = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.is_deleted == False,
    ).filter(
        User.phone.isnot(None),
    ).all()
    matched = next((e for e in employee if normalize_mobile(e.phone) == sender_phone), None)

    newly_opted_in = matched is not None and matched.whatsapp_opt_in_status in ("PENDING", "MISMATCH")
    if newly_opted_in:
        matched.whatsapp_opt_in_status = "OPTED_IN"
        matched.opt_in_source = "QR"
        matched.opt_in_at = __import__("datetime").datetime.utcnow()
        matched.matched_phone = sender_phone
        db.add(WhatsAppConsentEvent(
            tenant_id=tenant.id, employee_id=matched.id,
            event_type="OPT_IN_RECEIVED", phone_number=sender_phone,
            gupshup_message_id=gupshup_message_id, raw_webhook_payload=body,
            source="QR",
        ))
    elif matched:
        # Already OPTED_IN / MANUALLY_VERIFIED — log the trail, don't change status.
        db.add(WhatsAppConsentEvent(
            tenant_id=tenant.id, employee_id=matched.id,
            event_type="OPT_IN_RECEIVED", phone_number=sender_phone,
            gupshup_message_id=gupshup_message_id, raw_webhook_payload=body,
            source="QR",
        ))
    else:
        # No match — still log for audit/troubleshooting (Section 6.1).
        db.add(WhatsAppConsentEvent(
            tenant_id=tenant.id, employee_id=None,
            event_type="OPT_IN_RECEIVED", phone_number=sender_phone,
            gupshup_message_id=gupshup_message_id, raw_webhook_payload=body,
            source="QR",
        ))
    db.commit()
    if newly_opted_in:
        from .notifications import send_whatsapp_for_optin_confirmed
        send_whatsapp_for_optin_confirmed(db, tenant.id, matched)


def _handle_opt_out(db: Session, tenant: Tenant, body: dict, payload: dict):
    """Section 6.2 — opt-out handling."""
    sender_raw = payload.get("phone") or payload.get("sender", {}).get("phone") or ""
    if not sender_raw:
        return
    sender_phone = normalize_mobile(str(sender_raw))
    employees = db.query(User).filter(
        User.tenant_id == tenant.id, User.is_deleted == False, User.phone.isnot(None),
    ).all()
    matched = next((e for e in employees if normalize_mobile(e.phone) == sender_phone), None)
    if matched:
        matched.whatsapp_opt_in_status = "OPTED_OUT"
    db.add(WhatsAppConsentEvent(
        tenant_id=tenant.id, employee_id=matched.id if matched else None,
        event_type="OPT_OUT_RECEIVED", phone_number=sender_phone,
        raw_webhook_payload=body, source="QR",
    ))
    db.commit()


def _handle_status_event(db: Session, tenant: Tenant, payload: dict):
    """Section 6.3 — outbound delivery-status history append."""
    gs_message_id = payload.get("id") or payload.get("gsId")
    status = (payload.get("type") or "").lower()
    if not gs_message_id or not status:
        return
    log_row = db.query(WhatsAppMessageLog).filter(
        WhatsAppMessageLog.tenant_id == tenant.id,
    ).order_by(WhatsAppMessageLog.created_at.desc()).all()
    # Best-effort match: Gupshup message id isn't stored on send today (send
    # response wasn't parsed for it in gupshup.py) — flagged limitation, see
    # summary. Fall back to no-op if we can't identify the row.
    match = None
    for row in log_row:
        payloads = row.raw_status_webhook_payloads or []
        if any(p.get("id") == gs_message_id or p.get("gsId") == gs_message_id for p in payloads):
            match = row
            break
    if not match:
        return
    history = list(match.delivery_status_history or [])
    history.append({"status": status, "timestamp": payload.get("timestamp")})
    match.delivery_status_history = history
    raw = list(match.raw_status_webhook_payloads or [])
    raw.append(payload)
    match.raw_status_webhook_payloads = raw
    db.commit()
