"""Phase 0.6 — plan/Setup feature gating for /api/v1.

The website gates entire feature areas (FMS, Inventory, Sales, etc.) per
tenant via app/constants.py's has_feature()/TenantFeatureOverride, driven by
Setup > Access Control. The API layer never checked this — a mobile client
could reach any built endpoint regardless of what a tenant's plan/Setup
config allows on the website. This closes that gap using the exact same
has_feature() helper, so Setup stays the single source of truth for both
clients rather than drifting into two separate gating systems.
"""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..constants import feature_label, has_feature
from ..database import Tenant, User, get_db
from .security import get_current_api_user


def require_feature(feature_name: str):
    """FastAPI dependency: 403s if the caller's tenant doesn't have
    feature_name enabled (per plan or Setup > Access Control override)."""

    def _dep(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)) -> User:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if not tenant or not has_feature(tenant, feature_name, db):
            raise HTTPException(status_code=403, detail=f"{feature_label(feature_name)} is not enabled for your workspace.")
        return user

    return _dep
