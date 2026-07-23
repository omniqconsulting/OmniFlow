"""Shared helpers for the sales_* modules (orders, contacts, catalog, pricing, inventory)."""

from fastapi import HTTPException
from sqlalchemy.orm import Session


def get_or_404(db: Session, model, obj_id: str, tenant_id: str, label: str):
    """Tenant-scoped lookup by primary key, excluding soft-deleted rows.

    Mirrors the get_X_or_404 helpers duplicated across the sales_* modules:
    same filter shape (id + tenant_id + is_deleted == False), same 404 shape.
    """
    obj = db.query(model).filter(
        model.id == obj_id,
        model.tenant_id == tenant_id,
        model.is_deleted == False,
    ).first()
    if not obj:
        raise HTTPException(404, f"{label} not found")
    return obj
