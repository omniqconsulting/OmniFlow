"""
Employee documents & gadgets — KYC-style proofs and client-issued gadget records
attached to individual employee profiles. Both support batch submission
(multiple documents / multiple gadgets in a single request).
"""
from typing import List

from fastapi import APIRouter, Depends, Form, File, UploadFile, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .database import get_db, User, EmployeeDocument, EmployeeGadget, EmployeeGadgetDocument
from .auth import require_admin_or_pm as require_admin
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


def _reopen(emp_id: str, msg: str) -> RedirectResponse:
    """Redirect back to the employee list with the Documents & Gadgets panel
    for this employee still expanded, instead of collapsing on every action."""
    return RedirectResponse(f"/employees?msg={msg}&open_docs={emp_id}", status_code=303)


async def _check_doc_constraints(file: UploadFile):
    """Validate size/type without persisting — lets a whole batch be checked
    before anything is written to disk."""
    content = await file.read()
    if len(content) > MAX_DOC_MB * 1024 * 1024:
        raise HTTPException(413, f"'{file.filename}' is too large. Max {MAX_DOC_MB} MB.")
    ct = (file.content_type or "").lower()
    if ct not in ALLOWED_DOC_TYPES:
        raise HTTPException(400, f"'{file.filename}': only JPG, PNG, or PDF files are allowed.")
    await file.seek(0)


# ── Employee documents (identity/address proof, etc.) — batch upload ─────────

@router.post("/{emp_id}/documents")
async def upload_employee_documents(
    emp_id: str,
    doc_type: List[str] = Form(...),
    label: List[str] = Form(default=[]),
    file: List[UploadFile] = File(...),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    emp = _get_employee(db, emp_id, user.tenant_id)
    rows = [(dt, lbl, f) for dt, lbl, f in
             zip(doc_type, label + [""] * (len(doc_type) - len(label)), file)
             if f and f.filename]
    if not rows:
        raise HTTPException(400, "Select at least one file to upload")
    for dt, _lbl, _f in rows:
        if dt not in DOC_TYPE_LABELS:
            raise HTTPException(400, "Invalid document type")
    for _dt, _lbl, f in rows:
        await _check_doc_constraints(f)
    for dt, lbl, f in rows:
        info = await save_upload(f, user.tenant_id, allowed_kinds=("image", "pdf"), private=True)
        db.add(EmployeeDocument(
            tenant_id=user.tenant_id, user_id=emp.id, doc_type=dt,
            label=lbl or None, file_name=info["file_name"], file_path=info["file_path"],
            file_type=info["file_type"], file_size=info["file_size"], uploaded_by=user.id,
        ))
    db.commit()
    plural = "s" if len(rows) > 1 else ""
    return _reopen(emp.id, f"{len(rows)}+document{plural}+uploaded+for+{emp.name}")


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
    return _reopen(emp.id, "Document+removed")


# ── Employee gadgets (client-provided devices) — batch create ────────────────
# Multiple gadget rows arrive as indexed fields (gadget_name_0, files_0,
# gadget_name_1, files_1, ...) since each row needs its own file list, which
# plain FastAPI Form/File params can't express for a variable number of rows.

@router.post("/{emp_id}/gadgets")
async def create_employee_gadgets(
    emp_id: str, request: Request,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    emp = _get_employee(db, emp_id, user.tenant_id)
    form = await request.form()
    indices = sorted({
        int(k.rsplit("_", 1)[1]) for k in form.keys()
        if k.startswith("gadget_name_") and k.rsplit("_", 1)[1].isdigit()
    })
    rows = []
    for i in indices:
        name = (form.get(f"gadget_name_{i}") or "").strip()
        if not name:
            continue
        files = [f for f in form.getlist(f"files_{i}") if getattr(f, "filename", "")]
        rows.append({
            "name": name,
            "serial": (form.get(f"serial_number_{i}") or "").strip() or None,
            "provided": (form.get(f"provided_by_{i}") or "").strip() or None,
            "notes": (form.get(f"notes_{i}") or "").strip() or None,
            "files": files,
        })
    if not rows:
        raise HTTPException(400, "Enter at least one gadget name")
    for row in rows:
        for f in row["files"]:
            await _check_doc_constraints(f)
    for row in rows:
        gadget = EmployeeGadget(
            tenant_id=user.tenant_id, user_id=emp.id, gadget_name=row["name"],
            serial_number=row["serial"], provided_by=row["provided"],
            notes=row["notes"], created_by=user.id,
        )
        db.add(gadget)
        db.flush()
        for f in row["files"]:
            info = await save_upload(f, user.tenant_id, allowed_kinds=("image", "pdf"), private=True)
            db.add(EmployeeGadgetDocument(
                gadget_id=gadget.id, file_name=info["file_name"], file_path=info["file_path"],
                file_type=info["file_type"], file_size=info["file_size"],
            ))
    db.commit()
    plural = "s" if len(rows) > 1 else ""
    return _reopen(emp.id, f"{len(rows)}+gadget{plural}+added+for+{emp.name}")


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
    files = [f for f in files if f and f.filename]
    for f in files:
        await _check_doc_constraints(f)
    for f in files:
        info = await save_upload(f, user.tenant_id, allowed_kinds=("image", "pdf"), private=True)
        db.add(EmployeeGadgetDocument(
            gadget_id=gadget.id, file_name=info["file_name"], file_path=info["file_path"],
            file_type=info["file_type"], file_size=info["file_size"],
        ))
    db.commit()
    return _reopen(emp.id, "Document+added")


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
    return _reopen(emp.id, "Gadget+removed")


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
    return _reopen(emp.id, "Document+removed")
