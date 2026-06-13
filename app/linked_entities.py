"""
Phase 3 — Linked Entity System.

Provides:
  - _get_linked_entity_options(db, tenant_id) → dict used in form templates
  - API routes for creating and reading LinkedEntityReference records
"""
from __future__ import annotations

import json as _json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .database import (
    get_db, new_id,
    User, Tenant,
    Customer, EndProduct, CustomReferenceList, CustomReferenceItem,
    Material, LinkedEntityReference,
)
from .auth import get_current_user

router = APIRouter()


# ── P3-01: Helper ─────────────────────────────────────────────────────────────

def get_linked_entity_options(db: Session, tenant_id: str) -> dict:
    """
    Returns a dict of entity_type → list of {id, label, detail} dicts.
    Only includes types that have at least one active record for this tenant.
    Materials and Vendors pulled from inventory; Customers/EndProducts/CustomLists
    from setup tables.
    """
    options: dict[str, list[dict]] = {}

    customers = db.query(Customer).filter(
        Customer.tenant_id == tenant_id,
        Customer.is_deleted == False,
        Customer.is_active == True,
    ).order_by(Customer.name).all()
    if customers:
        options["CUSTOMER"] = [
            {
                "id": c.id, "label": c.name,
                "detail": _fmt([c.contact_person, c.phone, c.email, c.address]),
            }
            for c in customers
        ]

    products = db.query(EndProduct).filter(
        EndProduct.tenant_id == tenant_id,
        EndProduct.is_deleted == False,
        EndProduct.is_active == True,
    ).order_by(EndProduct.name).all()
    if products:
        options["END_PRODUCT"] = [
            {
                "id": p.id, "label": p.name,
                "detail": _fmt([p.sku_code and f"SKU: {p.sku_code}", p.unit and f"Unit: {p.unit}", p.description]),
            }
            for p in products
        ]

    materials = db.query(Material).filter(
        Material.tenant_id == tenant_id,
        Material.is_deleted == False,
        Material.is_active == True,
    ).order_by(Material.name).all()
    if materials:
        options["MATERIAL"] = [
            {
                "id": m.id, "label": m.name,
                "detail": _fmt([m.unit and f"Unit: {m.unit}", m.supplier and f"Supplier: {m.supplier}"]),
            }
            for m in materials
        ]

    # Vendors: distinct non-empty supplier values from the materials table
    vendors = db.query(Material.supplier).filter(
        Material.tenant_id == tenant_id,
        Material.is_deleted == False,
        Material.supplier != None,
        Material.supplier != "",
    ).distinct().all()
    if vendors:
        options["VENDOR"] = [
            {"id": v[0], "label": v[0], "detail": ""}
            for v in vendors if v[0]
        ]

    custom_lists = db.query(CustomReferenceList).filter(
        CustomReferenceList.tenant_id == tenant_id,
        CustomReferenceList.is_deleted == False,
        CustomReferenceList.is_active == True,
    ).order_by(CustomReferenceList.list_name).all()
    for lst in custom_lists:
        active_items = [i for i in lst.items if not i.is_deleted and i.is_active]
        if active_items:
            options[f"CUSTOM_LIST:{lst.id}:{lst.list_name}"] = [
                {"id": item.id, "label": item.value, "detail": ""}
                for item in active_items
            ]

    return options


def _fmt(parts: list) -> str:
    return " · ".join(p for p in parts if p)


def save_linked_entities_from_form(
    db: Session,
    form: dict,
    parent_type: str,
    parent_id: str,
    tenant_id: str,
    user_id: str,
) -> None:
    """
    Parse linked entity fields from a form submission dict and create
    LinkedEntityReference rows. Called from ticket/checklist/FMS creation routes.

    Expected form keys (all optional):
      linked_customer       → Customer id
      linked_end_product    → EndProduct id
      linked_material       → Material id
      linked_vendor         → vendor name string (used as both id and label)
      linked_custom_<id>    → CustomReferenceItem id
      linked_other          → free text
    """
    def _add(entity_type: str, entity_id, label: str, custom_text: str = None):
        if not entity_id and not custom_text:
            return
        db.add(LinkedEntityReference(
            tenant_id=tenant_id,
            parent_type=parent_type,
            parent_id=parent_id,
            entity_type=entity_type,
            entity_id=entity_id or None,
            entity_label=label or entity_id or custom_text or "",
            custom_text=custom_text,
            created_by_id=user_id,
        ))

    # Customer
    cust_id = (form.get("linked_customer") or "").strip()
    if cust_id:
        c = db.query(Customer).filter(Customer.id == cust_id, Customer.tenant_id == tenant_id).first()
        _add("CUSTOMER", cust_id, c.name if c else cust_id)

    # End Product
    prod_id = (form.get("linked_end_product") or "").strip()
    if prod_id:
        p = db.query(EndProduct).filter(EndProduct.id == prod_id, EndProduct.tenant_id == tenant_id).first()
        _add("END_PRODUCT", prod_id, p.name if p else prod_id)

    # Material
    mat_id = (form.get("linked_material") or "").strip()
    if mat_id:
        m = db.query(Material).filter(Material.id == mat_id, Material.tenant_id == tenant_id).first()
        _add("MATERIAL", mat_id, m.name if m else mat_id)

    # Vendor (stored as name string)
    vendor = (form.get("linked_vendor") or "").strip()
    if vendor:
        _add("VENDOR", vendor, vendor)

    # Custom lists — keys like linked_custom_<list_id>
    for key, val in form.items():
        if key.startswith("linked_custom_") and val and val.strip():
            item_id = val.strip()
            item = db.query(CustomReferenceItem).filter(
                CustomReferenceItem.id == item_id,
                CustomReferenceItem.tenant_id == tenant_id,
            ).first()
            _add("CUSTOM_LIST", item_id, item.value if item else item_id)

    # Other / free text
    other = (form.get("linked_other") or "").strip()
    if other:
        _add("OTHER", None, other, custom_text=other)

    db.flush()


# ── P3-02: API routes ─────────────────────────────────────────────────────────

@router.post("/api/linked-entities/add")
async def add_linked_entity(
    request: Request,
    parent_type: str = Form(...),
    parent_id: str = Form(...),
    entity_type: str = Form(...),
    entity_id: str = Form(""),
    entity_label: str = Form(""),
    custom_text: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if entity_type == "OTHER":
        if not custom_text.strip():
            return JSONResponse({"error": "custom_text required for OTHER type"}, status_code=400)
        label = custom_text.strip()
        eid = None
    else:
        if not entity_id:
            return JSONResponse({"error": "entity_id required"}, status_code=400)
        label = entity_label.strip() or entity_id
        eid = entity_id

    ref = LinkedEntityReference(
        tenant_id=user.tenant_id,
        parent_type=parent_type.upper(),
        parent_id=parent_id,
        entity_type=entity_type.upper(),
        entity_id=eid,
        entity_label=label,
        custom_text=custom_text.strip() or None,
        created_by_id=user.id,
    )
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return JSONResponse({"id": ref.id, "entity_label": ref.entity_label, "entity_type": ref.entity_type})


@router.get("/api/linked-entities/{parent_type}/{parent_id}")
def get_linked_entities(
    parent_type: str,
    parent_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    refs = db.query(LinkedEntityReference).filter(
        LinkedEntityReference.tenant_id == user.tenant_id,
        LinkedEntityReference.parent_type == parent_type.upper(),
        LinkedEntityReference.parent_id == parent_id,
    ).order_by(LinkedEntityReference.created_at).all()

    return JSONResponse([
        {
            "id": r.id,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "entity_label": r.entity_label,
            "custom_text": r.custom_text,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in refs
    ])


@router.post("/api/linked-entities/{ref_id}/delete")
def delete_linked_entity(
    ref_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ref = db.query(LinkedEntityReference).filter(
        LinkedEntityReference.id == ref_id,
        LinkedEntityReference.tenant_id == user.tenant_id,
    ).first()
    if ref:
        db.delete(ref)
        db.commit()
    return JSONResponse({"ok": True})
