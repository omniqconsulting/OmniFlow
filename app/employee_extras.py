"""
Employee documents & gadgets — KYC-style proofs and client-issued gadget records
attached to individual employee profiles.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, File, UploadFile, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .database import get_db, User, EmployeeDocument, EmployeeGadget, EmployeeGadgetDocument
from .auth import require_admin
from .uploads import save_upload

router = APIRouter(prefix="/employees", tags=["Employee Documents & Gadgets"])

ALLOWED_DOC_TYPES = {"image/jpeg", "image/png", "application/pdf"}
MAX_DOC_MB = 5

DOC_TYPE_LABELS = {
    "IDENTITY_PROOF": "Identity Proof",
    "ADDRESS_PROOF": "Address Proof",
    "OTHER": "Other",
}


def _get_employee(db: Session, emp_id: str, tenant_id: str) -> User:
    emp = db.query(User).filter(
        User.id == emp_id, User.tenant_id == tenant_id, User.is_deleted == False,
    ).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    return emp


async def _validate_and_save(file: UploadFile, tenant_id: str) -> dict:
    content = await file.read()
    if len(content) > MAX_DOC_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large. Max {MAX_DOC_MB} MB.")
    ct = (file.content_type or "").lower()
    if ct not in ALLOWED_DOC_TYPES:
        raise HTTPException(400, "Only JPG, PNG, or PDF files are allowed.")
    await file.seek(0)
    return await save_upload(file, tenant_id)


# ── Employee documents (identity/address proof, etc.) ────────────────────────

@router.post("/{emp_id}/documents")
async def upload_employee_document(
    emp_id: str,
    doc_type: str = Form(...),
    label: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    emp = _get_employee(db, emp_id, user.tenant_id)
    if doc_type not in DOC_TYPE_LABELS:
        raise HTTPException(400, "Invalid document type")
    info = await _validate_and_save(file, user.tenant_id)
    db.add(EmployeeDocument(
        tenant_id=user.tenant_id, user_id=emp.id, doc_type=doc_type,
        label=label or None, file_name=info["file_name"], file_path=info["file_path"],
        file_type=info["file_type"], file_size=info["file_size"], uploaded_by=user.id,
    ))
    db.commit()
    return RedirectResponse(f"/employees?msg=Document+uploaded+for+{emp.name}", status_code=303)


@router.post("/{emp_id}/documents/{doc_id}/delete")
def delete_employee_document(
    emp_id: str, doc_id: str,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    emp = _get_employee(db, emp_id, user.tenant_id)
    doc = db.query(EmployeeDocument).filter(
        EmployeeDocument.id == doc_id, EmployeeDocument.user_id == emp.id,
        EmployeeDocument.tenant_id == user.tenant_id,
    ).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    doc.is_deleted = True
    db.commit()
    return RedirectResponse(f"/employees?msg=Document+removed", status_code=303)


# ── Employee gadgets (client-provided devices) ────────────────────────────────

@router.post("/{emp_id}/gadgets")
async def create_employee_gadget(
    emp_id: str,
    gadget_name: str = Form(...),
    serial_number: str = Form(""),
    provided_by: str = Form(""),
    notes: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    emp = _get_employee(db, emp_id, user.tenant_id)
    gadget = EmployeeGadget(
        tenant_id=user.tenant_id, user_id=emp.id, gadget_name=gadget_name,
        serial_number=serial_number or None, provided_by=provided_by or None,
        notes=notes or None, created_by=user.id,
    )
    db.add(gadget)
    db.flush()
    for f in files:
        if not f or not f.filename:
            continue
        info = await _validate_and_save(f, user.tenant_id)
        db.add(EmployeeGadgetDocument(
            gadget_id=gadget.id, file_name=info["file_name"], file_path=info["file_path"],
            file_type=info["file_type"], file_size=info["file_size"],
        ))
    db.commit()
    return RedirectResponse(f"/employees?msg=Gadget+added+for+{emp.name}", status_code=303)


@router.post("/{emp_id}/gadgets/{gadget_id}/documents")
async def add_gadget_documents(
    emp_id: str, gadget_id: str,
    files: List[UploadFile] = File(default=[]),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    emp = _get_employee(db, emp_id, user.tenant_id)
    gadget = db.query(EmployeeGadget).filter(
        EmployeeGadget.id == gadget_id, EmployeeGadget.user_id == emp.id,
        EmployeeGadget.tenant_id == user.tenant_id, EmployeeGadget.is_deleted == False,
    ).first()
    if not gadget:
        raise HTTPException(404, "Gadget not found")
    for f in files:
        if not f or not f.filename:
            continue
        info = await _validate_and_save(f, user.tenant_id)
        db.add(EmployeeGadgetDocument(
            gadget_id=gadget.id, file_name=info["file_name"], file_path=info["file_path"],
            file_type=info["file_type"], file_size=info["file_size"],
        ))
    db.commit()
    return RedirectResponse(f"/employees?msg=Document+added", status_code=303)


@router.post("/{emp_id}/gadgets/{gadget_id}/delete")
def delete_employee_gadget(
    emp_id: str, gadget_id: str,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    emp = _get_employee(db, emp_id, user.tenant_id)
    gadget = db.query(EmployeeGadget).filter(
        EmployeeGadget.id == gadget_id, EmployeeGadget.user_id == emp.id,
        EmployeeGadget.tenant_id == user.tenant_id,
    ).first()
    if not gadget:
        raise HTTPException(404, "Gadget not found")
    gadget.is_deleted = True
    db.commit()
    return RedirectResponse(f"/employees?msg=Gadget+removed", status_code=303)


@router.post("/{emp_id}/gadgets/{gadget_id}/documents/{doc_id}/delete")
def delete_gadget_document(
    emp_id: str, gadget_id: str, doc_id: str,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    emp = _get_employee(db, emp_id, user.tenant_id)
    gadget = db.query(EmployeeGadget).filter(
        EmployeeGadget.id == gadget_id, EmployeeGadget.user_id == emp.id,
        EmployeeGadget.tenant_id == user.tenant_id,
    ).first()
    if not gadget:
        raise HTTPException(404, "Gadget not found")
    doc = db.query(EmployeeGadgetDocument).filter(
        EmployeeGadgetDocument.id == doc_id, EmployeeGadgetDocument.gadget_id == gadget.id,
    ).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()
    return RedirectResponse(f"/employees?msg=Document+removed", status_code=303)
