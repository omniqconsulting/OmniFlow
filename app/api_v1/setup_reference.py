"""Setup — reference-data CRUD for the native app: Branches, Departments,
Customers, Vendors, Raw Materials, End Products, Custom Lists, Units of
Measure. Mirrors the exact validation/limits the website's /setup/* routes
apply (app/main.py) against the same tables, so a record created here shows
up identically in tickets/FMS/checklists/sales pickers and vice versa —
there's only ever one Branch/Customer/Vendor table, not a mobile-only copy.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..constants import within_limit
from ..database import (
    Branch,
    CustomReferenceItem,
    CustomReferenceList,
    Customer,
    Department,
    EndProduct,
    RawMaterial,
    Tenant,
    UnitOfMeasure,
    User,
    Vendor,
    get_db,
)
from .security import get_current_api_user

router = APIRouter(prefix="/setup", tags=["Setup"])


def _require_admin_or_pm(user: User = Depends(get_current_api_user)) -> User:
    if user.role not in ("ADMIN", "PRODUCT_MANAGER"):
        raise HTTPException(status_code=403, detail="Admin or Product Manager only")
    return user


# ── Branches ─────────────────────────────────────────────────────────────

class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    address: Optional[str] = None
    weekly_off_days: list[int] = []


class BranchIn(BaseModel):
    name: str
    address: Optional[str] = ""
    weekly_off_days: list[int] = []


def _branch_out(b: Branch) -> BranchOut:
    try:
        days = json.loads(b.weekly_off_days) if b.weekly_off_days else [6]
    except (ValueError, TypeError):
        days = [6]
    return BranchOut(id=b.id, name=b.name, address=b.address, weekly_off_days=days)


@router.get("/branches", response_model=list[BranchOut])
def list_branches(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    rows = db.query(Branch).filter(Branch.tenant_id == user.tenant_id, Branch.is_deleted == False).order_by(Branch.name).all()
    return [_branch_out(b) for b in rows]


@router.post("/branches", response_model=BranchOut)
def create_branch(payload: BranchIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    current = db.query(Branch).filter(Branch.tenant_id == user.tenant_id, Branch.is_deleted == False).count()
    if not within_limit(tenant, "max_branches", current):
        raise HTTPException(status_code=403, detail="Branch limit reached for your plan — upgrade to add more.")
    branch = Branch(
        tenant_id=user.tenant_id, name=payload.name.strip(), address=(payload.address or "").strip(),
        weekly_off_days=json.dumps(payload.weekly_off_days or [6]),
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return _branch_out(branch)


@router.put("/branches/{branch_id}", response_model=BranchOut)
def update_branch(branch_id: str, payload: BranchIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    b = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id, Branch.is_deleted == False).first()
    if not b:
        raise HTTPException(status_code=404, detail="Branch not found")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    b.name = payload.name.strip()
    b.address = (payload.address or "").strip()
    b.weekly_off_days = json.dumps(payload.weekly_off_days or [6])
    db.commit()
    db.refresh(b)
    return _branch_out(b)


@router.delete("/branches/{branch_id}", status_code=204)
def delete_branch(branch_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    b = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id).first()
    if b:
        b.is_deleted = True
        db.commit()
    return None


# ── Departments ──────────────────────────────────────────────────────────

class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None


class DepartmentIn(BaseModel):
    name: str
    branch_id: Optional[str] = None


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    rows = (
        db.query(Department)
        .filter(Department.tenant_id == user.tenant_id, Department.is_deleted == False)
        .order_by(Department.name)
        .all()
    )
    return [
        DepartmentOut(id=d.id, name=d.name, branch_id=d.branch_id, branch_name=d.branch.name if d.branch else None)
        for d in rows
    ]


@router.post("/departments", response_model=DepartmentOut)
def create_department(payload: DepartmentIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    if payload.branch_id:
        branch = db.query(Branch).filter(Branch.id == payload.branch_id, Branch.tenant_id == user.tenant_id).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
    d = Department(tenant_id=user.tenant_id, name=payload.name.strip(), branch_id=payload.branch_id)
    db.add(d)
    db.commit()
    db.refresh(d)
    return DepartmentOut(id=d.id, name=d.name, branch_id=d.branch_id, branch_name=d.branch.name if d.branch else None)


@router.put("/departments/{department_id}", response_model=DepartmentOut)
def update_department(department_id: str, payload: DepartmentIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    d = db.query(Department).filter(Department.id == department_id, Department.tenant_id == user.tenant_id, Department.is_deleted == False).first()
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    d.name = payload.name.strip()
    d.branch_id = payload.branch_id
    db.commit()
    db.refresh(d)
    return DepartmentOut(id=d.id, name=d.name, branch_id=d.branch_id, branch_name=d.branch.name if d.branch else None)


@router.delete("/departments/{department_id}", status_code=204)
def delete_department(department_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    d = db.query(Department).filter(Department.id == department_id, Department.tenant_id == user.tenant_id).first()
    if d:
        d.is_deleted = True
        db.commit()
    return None


# ── Generic simple reference entities (Customers/Vendors/Raw Materials/End Products) ──

class ContactEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool = True


class ContactEntityIn(BaseModel):
    name: str
    contact_person: Optional[str] = ""
    phone: Optional[str] = ""
    email: Optional[str] = ""
    address: Optional[str] = ""
    notes: Optional[str] = ""
    is_active: bool = True


def _contact_router(path: str, model, label: str) -> APIRouter:
    sub = APIRouter(prefix=f"/setup/{path}", tags=["Setup"])

    @sub.get("", response_model=list[ContactEntityOut])
    def list_rows(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
        rows = db.query(model).filter(model.tenant_id == user.tenant_id, model.is_deleted == False).order_by(model.name).all()
        return rows

    @sub.post("", response_model=ContactEntityOut)
    def create_row(payload: ContactEntityIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
        if not payload.name.strip():
            raise HTTPException(status_code=422, detail="Name is required")
        row = model(
            tenant_id=user.tenant_id, name=payload.name.strip(),
            contact_person=payload.contact_person, phone=payload.phone, email=payload.email,
            address=payload.address, notes=payload.notes, is_active=payload.is_active,
            created_by_id=user.id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @sub.put("/{row_id}", response_model=ContactEntityOut)
    def update_row(row_id: str, payload: ContactEntityIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
        row = db.query(model).filter(model.id == row_id, model.tenant_id == user.tenant_id, model.is_deleted == False).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"{label} not found")
        if not payload.name.strip():
            raise HTTPException(status_code=422, detail="Name is required")
        row.name = payload.name.strip()
        row.contact_person = payload.contact_person
        row.phone = payload.phone
        row.email = payload.email
        row.address = payload.address
        row.notes = payload.notes
        row.is_active = payload.is_active
        db.commit()
        db.refresh(row)
        return row

    @sub.delete("/{row_id}", status_code=204)
    def delete_row(row_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
        row = db.query(model).filter(model.id == row_id, model.tenant_id == user.tenant_id).first()
        if row:
            row.is_deleted = True
            db.commit()
        return None

    return sub


customers_router = _contact_router("customers", Customer, "Customer")
vendors_router = _contact_router("vendors", Vendor, "Vendor")


# ── Raw Materials (different shape: unit/description/major_supplier) ──────

class RawMaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    unit: Optional[str] = None
    description: Optional[str] = None
    major_supplier: Optional[str] = None
    is_active: bool = True


class RawMaterialIn(BaseModel):
    name: str
    unit: Optional[str] = ""
    description: Optional[str] = ""
    major_supplier: Optional[str] = ""
    is_active: bool = True


materials_router = APIRouter(prefix="/setup/materials", tags=["Setup"])


@materials_router.get("", response_model=list[RawMaterialOut])
def list_materials(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    return db.query(RawMaterial).filter(RawMaterial.tenant_id == user.tenant_id, RawMaterial.is_deleted == False).order_by(RawMaterial.name).all()


@materials_router.post("", response_model=RawMaterialOut)
def create_material(payload: RawMaterialIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    row = RawMaterial(
        tenant_id=user.tenant_id, name=payload.name.strip(), unit=payload.unit,
        description=payload.description, major_supplier=payload.major_supplier,
        is_active=payload.is_active, created_by_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@materials_router.put("/{row_id}", response_model=RawMaterialOut)
def update_material(row_id: str, payload: RawMaterialIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(RawMaterial).filter(RawMaterial.id == row_id, RawMaterial.tenant_id == user.tenant_id, RawMaterial.is_deleted == False).first()
    if not row:
        raise HTTPException(status_code=404, detail="Raw material not found")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    row.name = payload.name.strip()
    row.unit = payload.unit
    row.description = payload.description
    row.major_supplier = payload.major_supplier
    row.is_active = payload.is_active
    db.commit()
    db.refresh(row)
    return row


@materials_router.delete("/{row_id}", status_code=204)
def delete_material(row_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(RawMaterial).filter(RawMaterial.id == row_id, RawMaterial.tenant_id == user.tenant_id).first()
    if row:
        row.is_deleted = True
        db.commit()
    return None


# ── End Products ────────────────────────────────────────────────────────

class EndProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    sku_code: Optional[str] = None
    unit: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True


class EndProductIn(BaseModel):
    name: str
    sku_code: Optional[str] = ""
    unit: Optional[str] = ""
    description: Optional[str] = ""
    is_active: bool = True


products_router = APIRouter(prefix="/setup/products", tags=["Setup"])


@products_router.get("", response_model=list[EndProductOut])
def list_products(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    return db.query(EndProduct).filter(EndProduct.tenant_id == user.tenant_id, EndProduct.is_deleted == False).order_by(EndProduct.name).all()


@products_router.post("", response_model=EndProductOut)
def create_product(payload: EndProductIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    row = EndProduct(
        tenant_id=user.tenant_id, name=payload.name.strip(), sku_code=payload.sku_code, unit=payload.unit,
        description=payload.description, is_active=payload.is_active, created_by_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@products_router.put("/{row_id}", response_model=EndProductOut)
def update_product(row_id: str, payload: EndProductIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(EndProduct).filter(EndProduct.id == row_id, EndProduct.tenant_id == user.tenant_id, EndProduct.is_deleted == False).first()
    if not row:
        raise HTTPException(status_code=404, detail="End product not found")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    row.name = payload.name.strip()
    row.sku_code = payload.sku_code
    row.unit = payload.unit
    row.description = payload.description
    row.is_active = payload.is_active
    db.commit()
    db.refresh(row)
    return row


@products_router.delete("/{row_id}", status_code=204)
def delete_product(row_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(EndProduct).filter(EndProduct.id == row_id, EndProduct.tenant_id == user.tenant_id).first()
    if row:
        row.is_deleted = True
        db.commit()
    return None


# ── Units of Measure ────────────────────────────────────────────────────

class UomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    abbreviation: str
    is_active: bool = True


class UomIn(BaseModel):
    name: str
    abbreviation: str
    is_active: bool = True


uom_router = APIRouter(prefix="/setup/uom", tags=["Setup"])


@uom_router.get("", response_model=list[UomOut])
def list_uoms(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    return db.query(UnitOfMeasure).filter(UnitOfMeasure.tenant_id == user.tenant_id, UnitOfMeasure.is_deleted == False).order_by(UnitOfMeasure.name).all()


@uom_router.post("", response_model=UomOut)
def create_uom(payload: UomIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    if not payload.name.strip() or not payload.abbreviation.strip():
        raise HTTPException(status_code=422, detail="Name and abbreviation are required")
    row = UnitOfMeasure(tenant_id=user.tenant_id, name=payload.name.strip(), abbreviation=payload.abbreviation.strip(), is_active=payload.is_active)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@uom_router.put("/{row_id}", response_model=UomOut)
def update_uom(row_id: str, payload: UomIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(UnitOfMeasure).filter(UnitOfMeasure.id == row_id, UnitOfMeasure.tenant_id == user.tenant_id, UnitOfMeasure.is_deleted == False).first()
    if not row:
        raise HTTPException(status_code=404, detail="Unit not found")
    if not payload.name.strip() or not payload.abbreviation.strip():
        raise HTTPException(status_code=422, detail="Name and abbreviation are required")
    row.name = payload.name.strip()
    row.abbreviation = payload.abbreviation.strip()
    row.is_active = payload.is_active
    db.commit()
    db.refresh(row)
    return row


@uom_router.delete("/{row_id}", status_code=204)
def delete_uom(row_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(UnitOfMeasure).filter(UnitOfMeasure.id == row_id, UnitOfMeasure.tenant_id == user.tenant_id).first()
    if row:
        row.is_deleted = True
        db.commit()
    return None


# ── Custom Reference Lists (+ items) ────────────────────────────────────

class RefItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    value: str
    sort_order: int
    is_active: bool = True


class RefListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    list_name: str
    items: list[RefItemOut] = []


class RefListIn(BaseModel):
    list_name: str


class RefItemIn(BaseModel):
    value: str
    sort_order: int = 0


lists_router = APIRouter(prefix="/setup/lists", tags=["Setup"])


@lists_router.get("", response_model=list[RefListOut])
def list_ref_lists(user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    rows = (
        db.query(CustomReferenceList)
        .filter(CustomReferenceList.tenant_id == user.tenant_id, CustomReferenceList.is_deleted == False)
        .order_by(CustomReferenceList.list_name)
        .all()
    )
    return [
        RefListOut(id=r.id, list_name=r.list_name, items=[i for i in r.items if not i.is_deleted])
        for r in rows
    ]


@lists_router.post("", response_model=RefListOut)
def create_ref_list(payload: RefListIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    if not payload.list_name.strip():
        raise HTTPException(status_code=422, detail="List name is required")
    row = CustomReferenceList(tenant_id=user.tenant_id, list_name=payload.list_name.strip(), created_by_id=user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return RefListOut(id=row.id, list_name=row.list_name, items=[])


@lists_router.delete("/{list_id}", status_code=204)
def delete_ref_list(list_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    row = db.query(CustomReferenceList).filter(CustomReferenceList.id == list_id, CustomReferenceList.tenant_id == user.tenant_id).first()
    if row:
        row.is_deleted = True
        db.commit()
    return None


@lists_router.post("/{list_id}/items", response_model=RefItemOut)
def add_ref_item(list_id: str, payload: RefItemIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    ref_list = db.query(CustomReferenceList).filter(CustomReferenceList.id == list_id, CustomReferenceList.tenant_id == user.tenant_id, CustomReferenceList.is_deleted == False).first()
    if not ref_list:
        raise HTTPException(status_code=404, detail="List not found")
    if not payload.value.strip():
        raise HTTPException(status_code=422, detail="Value is required")
    item = CustomReferenceItem(list_id=list_id, tenant_id=user.tenant_id, value=payload.value.strip(), sort_order=payload.sort_order)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@lists_router.put("/{list_id}/items/{item_id}", response_model=RefItemOut)
def update_ref_item(list_id: str, item_id: str, payload: RefItemIn, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    item = db.query(CustomReferenceItem).filter(
        CustomReferenceItem.id == item_id, CustomReferenceItem.list_id == list_id,
        CustomReferenceItem.tenant_id == user.tenant_id, CustomReferenceItem.is_deleted == False,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not payload.value.strip():
        raise HTTPException(status_code=422, detail="Value is required")
    item.value = payload.value.strip()
    item.sort_order = payload.sort_order
    db.commit()
    db.refresh(item)
    return item


@lists_router.delete("/{list_id}/items/{item_id}", status_code=204)
def delete_ref_item(list_id: str, item_id: str, user: User = Depends(_require_admin_or_pm), db: Session = Depends(get_db)):
    item = db.query(CustomReferenceItem).filter(
        CustomReferenceItem.id == item_id, CustomReferenceItem.list_id == list_id, CustomReferenceItem.tenant_id == user.tenant_id,
    ).first()
    if item:
        item.is_deleted = True
        db.commit()
    return None
