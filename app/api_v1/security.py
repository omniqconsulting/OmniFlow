"""Auth for /api/v1 — separate from app/auth.py's cookie-based web session.

Mobile clients get a short-lived JWT access token (Authorization: Bearer
header) plus a DB-backed opaque refresh token they exchange for a new
access token when it expires. The refresh token is what makes remote
session revocation possible — logout, or a future "sign out this device",
just deletes/expires the DB row; the JWT itself is never tracked or
revocable, which is fine given how short-lived it is.

Reuses app.auth's SECRET_KEY/ALGORITHM/verify_password so both auth paths
stay consistent with a single secret and hashing scheme.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from ..auth import ALGORITHM, SECRET_KEY
from ..database import ApiRefreshToken, User, get_db

ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 30

limiter = Limiter(key_func=get_remote_address)


def create_access_token(user_id: str, tenant_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_refresh_token(db: Session, user: User, device_label: str = None) -> str:
    raw_token = secrets.token_urlsafe(48)
    row = ApiRefreshToken(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=_hash_refresh_token(raw_token),
        device_label=device_label,
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_DAYS),
    )
    db.add(row)
    db.commit()
    return raw_token


def rotate_refresh_token(db: Session, raw_token: str) -> tuple[User, str]:
    """Verifies raw_token, revokes it, and issues a new one (rotation closes
    the replay window — if a refresh token is ever stolen and used, the
    legitimate client's next refresh attempt will fail because it's already
    revoked, which is a signal worth surfacing to the user in a future pass)."""
    token_hash = _hash_refresh_token(raw_token)
    row = db.query(ApiRefreshToken).filter(ApiRefreshToken.token_hash == token_hash).first()
    if not row or row.revoked_at is not None or row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user = db.query(User).filter(User.id == row.user_id, User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    row.revoked_at = datetime.utcnow()
    new_raw = create_refresh_token(db, user, device_label=row.device_label)
    db.commit()
    return user, new_raw


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    token_hash = _hash_refresh_token(raw_token)
    row = db.query(ApiRefreshToken).filter(ApiRefreshToken.token_hash == token_hash).first()
    if row and row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.commit()


def get_current_api_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header[len("Bearer "):]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = db.query(User).filter(User.id == payload.get("sub"), User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_api_admin(user: User = Depends(get_current_api_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def require_api_manager(user: User = Depends(get_current_api_user)) -> User:
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    return user
