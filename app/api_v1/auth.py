from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import verify_password
from ..database import LoginEvent, Tenant, User, get_db
from .schemas import UserOut
from .security import (
    ACCESS_TOKEN_MINUTES,
    create_access_token,
    create_refresh_token,
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
