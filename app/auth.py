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

class RedirectToLogin(Exception):
    """Raised by get_current_user_or_redirect so the app-level exception
    handler can turn it into a 302 to /login instead of a JSON 401 body."""
    pass

def get_current_user_or_redirect(request: Request, db: Session = Depends(get_db)) -> User:
    """Same auth check as get_current_user, but for HTML page routes: an
    unauthenticated/invalid session redirects to /login instead of
    returning a raw 401 JSON body (which is what a PWA launched from the
    home screen with no session would otherwise render as start_url)."""
    try:
        return get_current_user(request, db)
    except HTTPException:
        raise RedirectToLogin()

def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin only")
    return user

def require_manager(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    return user

def require_admin_or_redirect(user: User = Depends(get_current_user_or_redirect)) -> User:
    """require_admin for HTML page routes: missing/invalid session redirects
    to /login (via get_current_user_or_redirect); wrong role still raises a
    plain 403 (that's a real authorization error, not a login problem)."""
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin only")
    return user

def require_manager_or_redirect(user: User = Depends(get_current_user_or_redirect)) -> User:
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


def get_user_tabs(user, tenant, db: Session = None) -> list:
    """Effective nav tabs visible to this user, constrained by tenant-enabled tabs.
    ADMIN and users with no tab_access_json set (None) get every tenant-enabled
    tab — restriction is opt-in per employee/manager."""
    from .constants import get_tenant_enabled_tabs
    tenant_tabs = get_tenant_enabled_tabs(tenant, db)
    if user.role == "ADMIN" or not user.tab_access_json:
        return tenant_tabs
    try:
        selected = set(_json.loads(user.tab_access_json))
    except Exception:
        return tenant_tabs
    return [t for t in tenant_tabs if t in selected]


def get_nav_flags(db: Session, user, tenant=None) -> dict:
    """Return nav feature flags for base.html — the single source of truth for
    which tabs are visible, shared by every blueprint so the nav bar stays
    consistent no matter which route rendered the current page."""
    from .database import Tenant as _Tenant
    if user is None:
        return {"has_inventory": False, "has_tickets": True, "has_fms": False, "has_checklists": False, "has_sales": False, "has_inventory_module": False, "has_sales_analytics": False, "user_modules": []}
    try:
        from .constants import has_feature
        t = tenant or db.query(_Tenant).filter(_Tenant.id == user.tenant_id).first()
        modules = get_user_modules(user)
        # Per-employee/manager tab access — falls back to every tenant-enabled tab when unset
        user_tabs = get_user_tabs(user, t, db) if t else []
        return {
            "has_inventory":         has_feature(t, "INVENTORY",       db) if t else False,
            "has_tickets":           "TICKETS"    in user_tabs,
            "has_fms":               "FMS"        in user_tabs,
            "has_knowledge_repo":    "KNOWLEDGE"  in user_tabs,
            "has_checklists":        "CHECKLISTS" in user_tabs,
            "has_sales":             "SALES"     in modules and "SALES"     in user_tabs,
            "has_inventory_module":  "INVENTORY" in modules and "INVENTORY" in user_tabs,
            "has_sales_analytics":   (has_feature(t, "SALES_ANALYTICS", db) if t else False)
                                      and (has_feature(t, "SALES_MODULE", db) if t else False)
                                      and "SALES" in modules and user.role in ("ADMIN", "MANAGER")
                                      and "SALES_ANALYTICS" in user_tabs,
            "user_modules":          modules,
        }
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning("get_nav_flags failed: %s", _e)
        return {"has_inventory": False, "has_tickets": True, "has_fms": False, "has_knowledge_repo": False, "has_checklists": True, "has_sales": False, "has_inventory_module": False, "has_sales_analytics": False, "user_modules": []}


def require_module(module: str, feature: str, redirect_unauthenticated: bool = False):
    """Dependency factory: gates a route on both the tenant-level feature flag
    (SA toggle) and the user's own module access. Use per-blueprint, e.g.
    _require_sales = require_module("SALES", "SALES_MODULE").
    Pass redirect_unauthenticated=True for HTML page routes so a missing/invalid
    session redirects to /login instead of raising a raw 401 JSON body — role/
    feature-flag mismatches still raise a plain 403 either way."""
    _user_dep = get_current_user_or_redirect if redirect_unauthenticated else get_current_user
    def _dep(user: User = Depends(_user_dep), db: Session = Depends(get_db)) -> User:
        from .constants import has_feature
        tenant = db.query(Tenant).get(user.tenant_id)
        if not has_feature(tenant, feature, db):
            raise HTTPException(status_code=403, detail=f"{module.title()} module not enabled for this tenant")
        if not has_module(user, module):
            raise HTTPException(status_code=403, detail=f"{module.title()} module not enabled for this user")
        return user
    return _dep

