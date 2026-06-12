from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .database import get_db, User

SECRET_KEY = "factoryos-secret-key-change-in-production-32chars"
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

def require_store_manager(user: User = Depends(get_current_user)) -> User:
    """Phase 4: Store Manager or Admin can access inventory routes."""
    if user.role not in ("ADMIN", "STORE_MANAGER"):
        raise HTTPException(status_code=403, detail="Store Manager or Admin only")
    return user

def require_inventory_admin(user: User = Depends(get_current_user)) -> User:
    """Phase 4: Only Admin can perform catalogue management and PO approval."""
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin only")
    return user
