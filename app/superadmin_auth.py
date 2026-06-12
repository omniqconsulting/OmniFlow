"""
Super-admin JWT auth — Phase 0-H
Uses a separate cookie ('sa_token') and a separate secret so it is
completely isolated from tenant-user sessions.
"""
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .database import get_db, SuperAdmin

SA_SECRET = "omniflow-superadmin-secret-CHANGE-IN-PROD-64chars!!"
ALGORITHM  = "HS256"
COOKIE     = "sa_token"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def sa_hash(password: str) -> str:
    return pwd_context.hash(password)


def sa_verify(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def sa_create_token(sa_id: str) -> str:
    payload = {
        "sub": sa_id,
        "scope": "superadmin",
        "exp": datetime.utcnow() + timedelta(hours=8),
    }
    return jwt.encode(payload, SA_SECRET, algorithm=ALGORITHM)


def get_current_sa(request: Request, db: Session = Depends(get_db)) -> SuperAdmin:
    token = request.cookies.get(COOKIE)
    if not token:
        raise _redirect_exc()
    try:
        payload = jwt.decode(token, SA_SECRET, algorithms=[ALGORITHM])
        if payload.get("scope") != "superadmin":
            raise _redirect_exc()
        sa_id = payload.get("sub")
    except JWTError:
        raise _redirect_exc()
    sa = db.query(SuperAdmin).filter(SuperAdmin.id == sa_id,
                                     SuperAdmin.is_active == True).first()
    if not sa:
        raise _redirect_exc()
    return sa


def _redirect_exc():
    from fastapi import HTTPException
    # We raise a 302 redirect disguised as an HTTPException that the router catches.
    return HTTPException(status_code=302, headers={"Location": "/superadmin/login"})
