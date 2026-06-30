import json as _json
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .database import get_db, User, Tenant

SECRET_KEY = "omniflow-secret-key-change-in-production-32chars"
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_token(user_id: str, tenant_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=24),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin only")
    return user

def require_manager(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    return user

def get_user_modules(user) -> list:
    """Return list of module tags accessible to this user.
    Admin and Manager always get all modules."""
    if user.role in ("ADMIN", "MANAGER"):
        return ["SALES", "INVENTORY"]
    try:
        return _json.loads(user.module_access_json or "[]")
    except Exception:
        return []

def has_module(user, module: str) -> bool:
    return module in get_user_modules(user)


def require_module(module: str, feature: str):
    """Dependency factory: gates a route on both the tenant-level feature flag
    (SA toggle) and the user's own module access. Use per-blueprint, e.g.
    _require_sales = require_module("SALES", "SALES_MODULE")."""
    def _dep(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        from .constants import has_feature
        tenant = db.query(Tenant).get(user.tenant_id)
        if not has_feature(tenant, feature, db):
            raise HTTPException(status_code=403, detail=f"{module.title()} module not enabled for this tenant")
        if not has_module(user, module):
            raise HTTPException(status_code=403, detail=f"{module.title()} module not enabled for this user")
        return user
    return _dep

