from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import DeviceToken, User, get_db
from .schemas import UtcDateTime
from .security import get_current_api_user, limiter

router = APIRouter(prefix="/devices", tags=["Devices"])


class DeviceRegisterRequest(BaseModel):
    device_id: str
    expo_push_token: str
    platform: str


class DeviceRegisterOut(BaseModel):
    device_id: str
    platform: str
    last_seen_at: UtcDateTime


@router.post("/register", response_model=DeviceRegisterOut)
@limiter.limit("20/minute")
def register_device(request: Request, body: DeviceRegisterRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Phase 0.5-C: storage + registration only, upserts by device_id. No
    push-sending logic yet — that's a later phase once the app exists and
    can be tested end-to-end."""
    existing = db.query(DeviceToken).filter(
        DeviceToken.tenant_id == user.tenant_id, DeviceToken.device_id == body.device_id,
    ).first()
    now = datetime.utcnow()
    if existing:
        existing.user_id = user.id
        existing.expo_push_token = body.expo_push_token
        existing.platform = body.platform
        existing.last_seen_at = now
        db.commit()
        return DeviceRegisterOut(device_id=existing.device_id, platform=existing.platform, last_seen_at=existing.last_seen_at)

    row = DeviceToken(
        tenant_id=user.tenant_id, user_id=user.id, device_id=body.device_id,
        expo_push_token=body.expo_push_token, platform=body.platform,
        created_at=now, last_seen_at=now,
    )
    db.add(row)
    db.commit()
    return DeviceRegisterOut(device_id=row.device_id, platform=row.platform, last_seen_at=row.last_seen_at)


@router.delete("/{device_id}", status_code=204)
def unregister_device(device_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Called on logout so a signed-out device stops receiving this user's
    pushes — mirrors push.py's Web Push /push/unsubscribe."""
    db.query(DeviceToken).filter(
        DeviceToken.tenant_id == user.tenant_id, DeviceToken.user_id == user.id, DeviceToken.device_id == device_id,
    ).delete()
    db.commit()
    return None
