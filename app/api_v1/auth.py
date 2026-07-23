from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import hash_password, verify_password
from ..database import LoginEvent, Tenant, User, get_db, seed_default_uoms
from .schemas import UserOut
from .security import (
    ACCESS_TOKEN_MINUTES,
    create_access_token,
    create_refresh_token,
    get_current_api_user,
    limiter,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    slug: str
    phone: str
    password: str
    device_label: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    factory_name: str
    slug: str
    name: str
    phone: str
    password: str
    contact_email: str | None = None


class ProfileUpdateRequest(BaseModel):
    name: str
    phone: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_MINUTES * 60
    user: UserOut


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.slug == body.slug).first()
    if not tenant:
        raise HTTPException(status_code=401, detail="Factory not found")
    if getattr(tenant, "is_suspended", False):
        raise HTTPException(status_code=403, detail="This factory account has been suspended")
    user = db.query(User).filter(
        User.tenant_id == tenant.id, User.phone == body.phone, User.is_deleted == False
    ).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user.last_login = datetime.utcnow()
    db.add(LoginEvent(tenant_id=tenant.id, user_id=user.id))
    db.commit()

    access_token = create_access_token(user.id, tenant.id, user.role)
    refresh_token = create_refresh_token(db, user, device_label=body.device_label)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=user)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
def refresh(request: Request, body: RefreshRequest, db: Session = Depends(get_db)):
    user, new_refresh_token = rotate_refresh_token(db, body.refresh_token)
    access_token = create_access_token(user.id, user.tenant_id, user.role)
    return TokenResponse(access_token=access_token, refresh_token=new_refresh_token, user=user)


@router.post("/logout")
def logout(body: LogoutRequest, db: Session = Depends(get_db)):
    revoke_refresh_token(db, body.refresh_token)
    return {"ok": True}


@router.post("/register")
@limiter.limit("5/minute")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(Tenant).filter(Tenant.slug == body.slug).first():
        raise HTTPException(status_code=409, detail="Factory ID already taken")
    tenant = Tenant(
        name=body.factory_name, slug=body.slug,
        plan="TRIAL", is_approved=False,
        contact_name=body.name, contact_email=body.contact_email or None,
        trial_started_at=datetime.utcnow(),
    )
    db.add(tenant)
    db.flush()
    user = User(tenant_id=tenant.id, name=body.name, phone=body.phone,
                password_hash=hash_password(body.password), role="ADMIN")
    db.add(user)
    db.flush()
    seed_default_uoms(db, tenant.id)
    db.commit()

    # Lazy import to dodge the app.main <-> app.api_v1 import cycle (main.py
    # imports this router; these two WhatsApp notifiers live in main.py).
    from .. import main as _main
    _main._send_wa_registration_received(db, body.phone, body.name, body.factory_name)
    _main._send_wa_registration_alert_sa(body.factory_name, body.name, body.phone, tenant.id, db)

    return {"tenant_id": tenant.id, "slug": tenant.slug, "status": "pending_approval"}


@router.get("/check-slug")
def check_slug(slug: str, db: Session = Depends(get_db)):
    exists = db.query(Tenant).filter(Tenant.slug == slug).first() is not None
    return {"available": not exists}


@router.patch("/profile", response_model=UserOut)
def update_profile(body: ProfileUpdateRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    existing = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.phone == body.phone,
        User.id != user.id,
        User.is_deleted == False,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Phone number already in use by another account")
    user.name = body.name.strip()
    user.phone = body.phone.strip()
    db.commit()
    return user


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
