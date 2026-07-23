import json as _json
import os
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .database import get_db, User, Tenant

_DEV_SECRET_KEY = "omniflow-secret-key-change-in-production-32chars"
SECRET_KEY = os.environ.get("SECRET_KEY")

if not SECRET_KEY and os.environ.get("RENDER"):
    # Security audit Part 1/3: never silently sign JWTs with the well-known
    # dev default in production — anyone who's read this source (it's
    # public in git history regardless) could forge valid tokens.
    raise RuntimeError(
        "SECRET_KEY is not set on Render — refusing to fall back to the "
        "dev default in production. Set a real SECRET_KEY in the Render "
        "environment settings."
    )

SECRET_KEY = SECRET_KEY or _DEV_SECRET_KEY
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

def require_admin_or_pm(user: User = Depends(get_current_user)) -> User:
    """Gate for Setup + Employees routes: ADMIN has full access everywhere;
    PRODUCT_MANAGER is scoped to just this module (setup_routes.py,
    employee_extras.py) so they can manage platform config/employees without
    needing FMS/ticket/sales admin powers, which stay ADMIN/MANAGER-only."""
    if user.role not in ("ADMIN", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Admin only")
    return user

def require_admin_or_pm_or_redirect(user: User = Depends(get_current_user_or_redirect)) -> User:
    """require_admin_or_pm for HTML page routes — see require_admin_or_redirect."""
    if user.role not in ("ADMIN", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Admin only")
    return user

def require_manager_or_pm_or_redirect(user: User = Depends(get_current_user_or_redirect)) -> User:
    """require_manager_or_redirect plus PRODUCT_MANAGER — for the Employees
    page specifically, which PRODUCT_MANAGER must be able to view/manage."""
    if user.role not in ("ADMIN", "MANAGER", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    return user

def require_admin_or_pm_or_manager(user: User = Depends(get_current_user)) -> User:
    """require_admin_or_pm plus MANAGER — for employee-edit-style POST routes
    where a MANAGER may act, but only on their own direct reports (the
    ownership check happens in the route itself, not here)."""
    if user.role not in ("ADMIN", "MANAGER", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Admin, Manager or Product Manager only")
    return user

def require_manager_or_redirect(user: User = Depends(get_current_user_or_redirect)) -> User:
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    return user

def get_user_modules(user) -> list:
    """Return list of module tags accessible to this user.
    Admin, Manager and Product Manager always get all modules."""
    if user.role in ("ADMIN", "MANAGER", "PRODUCT_MANAGER"):
        return ["SALES", "INVENTORY"]
    try:
        return _json.loads(user.module_access_json or "[]")
    except Exception:
        return []

def has_module(user, module: str) -> bool:
    return module in get_user_modules(user)


def get_user_tabs(user, tenant, db: Session = None, for_setup: bool = False) -> list:
    """Effective nav tabs visible to this user, constrained by tenant-enabled tabs.
    ADMIN and users with no tab_access_json set (None) get every tenant-enabled
    tab — restriction is opt-in per employee/manager. PRODUCT_MANAGER is a
    fixed, non-configurable scope (Setup + Employees only, handled outside
    the nav-tabs list): with no explicit access rule it gets none of these
    extra tabs — except within Setup itself (for_setup=True), where PM must
    see the same tenant-enabled modules as ADMIN so the Setup page reconciles
    exactly between the two roles. If an Admin *does* grant a PM extra tabs
    via Setup > Access Control (tab_access_json gets set), that grant is
    respected instead of being silently ignored."""
    from .constants import get_tenant_enabled_tabs
    tenant_tabs = get_tenant_enabled_tabs(tenant, db)
    if user.role == "PRODUCT_MANAGER":
        if for_setup:
            return tenant_tabs
        if not user.tab_access_json:
            return []
        try:
            selected = set(_json.loads(user.tab_access_json))
        except Exception:
            return []
        return [t for t in tenant_tabs if t in selected]
    if user.role == "ADMIN" or not user.tab_access_json:
        return tenant_tabs
    try:
        selected = set(_json.loads(user.tab_access_json))
    except Exception:
        return tenant_tabs
    return [t for t in tenant_tabs if t in selected]


def get_nav_flags(db: Session, user, tenant=None, for_setup: bool = False) -> dict:
    """Return nav feature flags for base.html — the single source of truth for
    which tabs are visible, shared by every blueprint so the nav bar stays
    consistent no matter which route rendered the current page."""
    from .database import Tenant as _Tenant
    if user is None:
        return {"has_inventory": False, "has_tickets": True, "has_fms": False, "has_checklists": False, "has_sales": False, "has_inventory_module": False, "has_sales_analytics": False, "has_attendance": False, "user_modules": []}
    try:
        from .constants import has_feature
        t = tenant or db.query(_Tenant).filter(_Tenant.id == user.tenant_id).first()
        modules = get_user_modules(user)
        # Per-employee/manager tab access — falls back to every tenant-enabled tab when unset
        user_tabs = get_user_tabs(user, t, db, for_setup=for_setup) if t else []
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
            "has_attendance":        "ATTENDANCE" in user_tabs,
            "user_modules":          modules,
        }
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning("get_nav_flags failed: %s", _e)
        return {"has_inventory": False, "has_tickets": True, "has_fms": False, "has_knowledge_repo": False, "has_checklists": True, "has_sales": False, "has_inventory_module": False, "has_sales_analytics": False, "has_attendance": False, "user_modules": []}


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


def require_any_module(*module_features: tuple, redirect_unauthenticated: bool = False):
    """Dependency factory: gates a route on ANY of the given (module, feature) pairs —
    passes if the tenant has the feature enabled AND the user has the module for at
    least one pair. Use for shared surfaces like Catalog that both Sales and
    Inventory users need, e.g.
    _require_catalog = require_any_module(("SALES", "SALES_MODULE"), ("INVENTORY", "INVENTORY_MODULE"))."""
    _user_dep = get_current_user_or_redirect if redirect_unauthenticated else get_current_user
    def _dep(user: User = Depends(_user_dep), db: Session = Depends(get_db)) -> User:
        from .constants import has_feature
        tenant = db.query(Tenant).get(user.tenant_id)
        for module, feature in module_features:
            if has_feature(tenant, feature, db) and has_module(user, module):
                return user
        names = " or ".join(m.title() for m, _ in module_features)
        raise HTTPException(status_code=403, detail=f"{names} module not enabled for this user")
    return _dep

