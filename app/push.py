"""
Phase 6 — Web Push subscription management + send helper.
Third, additive notification channel alongside in-app (Notification row)
and WhatsApp/SMS (Phase 5) — does not replace either.
"""
import os
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .database import get_db, new_id, PushSubscription
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# Dev-only fallback keypair so push works out of the box locally.
# In production set VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_CLAIM_EMAIL.
_DEV_VAPID_PUBLIC_KEY = "BL7Z1eFBwdhsPYgRyzux7eSXftn-ggA5OiIuo3vh0MY3sD5zVS3osEul-K2wpchUncRTYLjmAyfVXqyNav-Wp3I"
_DEV_VAPID_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg+vLgnwtnfqVs/fWX\n"
    "0sSi2M+5QPcfKnibtai99dyJPjGhRANCAAS+2dXhQcHYbD2IEcs7se3kl37Z/oIA\n"
    "OToiLqN74dDGN7A+c1Ut6LBLpfitsKXIVJ3EU2C45gMn1V6sjWr/lqdy\n"
    "-----END PRIVATE KEY-----\n"
)

def _resolve_vapid_private_key(raw: str) -> str:
    """
    Accepts either a raw multi-line PEM string, or that same PEM
    base64-encoded onto a single line (for env-var UIs that don't support
    multi-line values). Returns a proper multi-line PEM string either way.
    """
    raw = raw.strip()
    if raw.startswith("-----BEGIN"):
        return raw
    import base64
    try:
        return base64.b64decode(raw).decode()
    except Exception:
        logger.warning("VAPID_PRIVATE_KEY is set but not valid PEM or base64 — falling back to dev key")
        return _DEV_VAPID_PRIVATE_KEY


VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", _DEV_VAPID_PUBLIC_KEY)
VAPID_PRIVATE_KEY = _resolve_vapid_private_key(
    os.environ.get("VAPID_PRIVATE_KEY", _DEV_VAPID_PRIVATE_KEY)
)
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@omniflow.app")


@router.get("/push/vapid-public-key")
def get_vapid_public_key(user=Depends(get_current_user)):
    return JSONResponse({"publicKey": VAPID_PUBLIC_KEY})


@router.post("/push/subscribe")
def subscribe(
    endpoint: str = Form(...),
    p256dh_key: str = Form(...),
    auth_key: str = Form(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(PushSubscription).filter(
        PushSubscription.user_id == user.id,
        PushSubscription.endpoint == endpoint,
    ).first()
    if not existing:
        db.add(PushSubscription(
            tenant_id=user.tenant_id,
            user_id=user.id,
            endpoint=endpoint,
            p256dh_key=p256dh_key,
            auth_key=auth_key,
        ))
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/push/unsubscribe")
def unsubscribe(
    endpoint: str = Form(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(PushSubscription).filter(
        PushSubscription.user_id == user.id,
        PushSubscription.endpoint == endpoint,
    ).delete()
    db.commit()
    return JSONResponse({"ok": True})


def send_push_for_user(db: Session, user_id: str, title: str, body: str = "", link: str = ""):
    """
    Best-effort Web Push fan-out to every device subscription for a user.
    Never raises back into the caller — a push failure must not break
    in-app notification creation or WhatsApp sends.
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return

    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    if not subs:
        return

    import json
    payload = json.dumps({"title": title, "body": body, "link": link})

    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh_key, "auth": sub.auth_key},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            )
            from datetime import datetime
            sub.last_used_at = datetime.utcnow()
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                db.delete(sub)
            else:
                logger.warning("Web push failed for user %s: %s", user_id, e)
        except Exception as e:
            logger.warning("Web push failed for user %s: %s", user_id, e)
    db.commit()
