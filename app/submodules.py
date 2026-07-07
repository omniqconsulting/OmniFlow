"""
Phase 3 — Sub-module Endpoints
3-A: PMS daily log
3-B: Dispatch records + POD
3-C: Invoice records
3-D: Material requisitions
3-E: Custom sub-module renderer
"""
import json as _json
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import (
    get_db, new_id,
    Tenant, User, Notification,
    FMSTicket, FMSStage, FMSEvent,
    PMSDailyLog, DispatchRecord, InvoiceRecord,
    CustomSubmoduleResponse,
    LibrarySubmoduleDefinition, TenantDeployedItem,
)
from .auth import get_current_user, get_current_user_or_redirect, require_admin, require_manager
from .labels import get_labels, DEFAULT_L
from .notifications import notify_fms_stage_transition
from .ws_manager import broadcast_sync, FMS_STAGE_TRANSITION

import os
from .templates_env import templates  # shared instance — has all filters

# Reuse same ORM-aware tojson encoder


router = APIRouter(prefix="/submodules", tags=["Submodules"])

SUBMODULE_TYPES = ["PMS", "DISPATCH", "INVOICE", "CUSTOM"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _redirect(path: str):
    return RedirectResponse(path, status_code=302)

def _get_ticket(db, ticket_id, tenant_id) -> FMSTicket:
    t = db.query(FMSTicket).filter(
        FMSTicket.id == ticket_id,
        FMSTicket.tenant_id == tenant_id,
        FMSTicket.is_deleted == False,
    ).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    return t

def _log_event(db, ticket_id, actor_id, event_type, detail=""):
    db.add(FMSEvent(
        ticket_id=ticket_id, actor_id=actor_id,
        event_type=event_type, detail=detail))

def _unread(db, user):
    return db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False).count()

def _ctx(request, user, db, **kw):
    L = get_labels(db, user.tenant_id) if user else DEFAULT_L
    return {"request": request, "user": user, "L": L,
            "unread": _unread(db, user), **kw}

def _admin_manager_ids(db, tenant_id):
    return [u.id for u in db.query(User).filter(
        User.tenant_id == tenant_id,
        User.role.in_(["ADMIN", "MANAGER"]),
        User.is_deleted == False).all()]

# Map sub_module_type → the fixed system library ID seeded in database.py
_BUILTIN_IDS = {
    "PMS":      "sys-pms-builtin",
    "DISPATCH": "sys-dispatch-builtin",
    "INVOICE":  "sys-invoice-builtin",
}

def _require_submodule(db, tenant_id: str, sub_module_type: str):
    """Raise 403 if the tenant does not have this sub-module deployed.
    CUSTOM sub-modules are checked by the caller via stage.deployed_submodule_id."""
    lib_id = _BUILTIN_IDS.get(sub_module_type)
    if not lib_id:
        return   # CUSTOM — no built-in gate here
    deployed = db.query(TenantDeployedItem).filter(
        TenantDeployedItem.tenant_id       == tenant_id,
        TenantDeployedItem.library_item_id == lib_id,
        TenantDeployedItem.item_type       == "submodule",
    ).first()
    if not deployed:
        raise HTTPException(
            403,
            f"The '{sub_module_type}' sub-module has not been enabled for your account. "
            "Contact your Super Admin to deploy it."
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3-A: PMS Sub-module
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/pms/{ticket_id}", response_class=HTMLResponse)
def pms_panel(ticket_id: str, request: Request,
              user: User = Depends(get_current_user_or_redirect),
              db: Session = Depends(get_db)):
    """3-A-2: Full PMS panel for a ticket."""
    _require_submodule(db, user.tenant_id, "PMS")
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    logs   = db.query(PMSDailyLog).filter(
        PMSDailyLog.ticket_id == ticket_id
    ).order_by(PMSDailyLog.log_date.desc()).all()

    # Cumulative qty done
    total_done = sum(l.qty_done for l in logs if l.event_type == "DAILY_LOG")
    target     = ticket.target_qty or 0
    pct        = int(total_done / target * 100) if target else 0

    # Has today's entry?
    today = date.today()
    today_entry = next((l for l in logs
                        if l.log_date == today and l.event_type == "DAILY_LOG"), None)

    can_revise_target = user.role in ("ADMIN", "MANAGER")

    return templates.TemplateResponse(request, "submodules/pms_panel.html", _ctx(
        request, user, db,
        ticket=ticket, logs=logs,
        total_done=total_done, target=target, pct=pct,
        today_entry=today_entry, today=today,
        can_revise_target=can_revise_target,
    ))


@router.post("/pms/{ticket_id}/log")
def pms_log_entry(
    ticket_id: str,
    qty_done: int = Form(...),
    has_blockers: bool = Form(False),
    comment: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """3-A-3: Submit a daily PMS log entry (immutable)."""
    _require_submodule(db, user.tenant_id, "PMS")
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    today  = date.today()

    # One entry per day per ticket (immutable — no update)
    existing = db.query(PMSDailyLog).filter(
        PMSDailyLog.ticket_id == ticket_id,
        PMSDailyLog.log_date  == today,
        PMSDailyLog.event_type == "DAILY_LOG",
    ).first()
    if existing:
        raise HTTPException(400, "A daily log entry already exists for today")

    db.add(PMSDailyLog(
        ticket_id=ticket_id, tenant_id=user.tenant_id,
        log_date=today, qty_done=qty_done,
        has_blockers=has_blockers, comment=comment.strip() or None,
        event_type="DAILY_LOG", actor_id=user.id,
    ))
    _log_event(db, ticket_id, user.id, "COMMENT",
               f"PMS log: {qty_done} {ticket.qty_unit or 'units'}"
               + (" [BLOCKERS]" if has_blockers else ""))
    db.commit()

    # Real-time broadcast to admins + managers
    notif_ids = _admin_manager_ids(db, user.tenant_id)
    broadcast_sync(user.tenant_id, notif_ids, FMS_STAGE_TRANSITION, {
        "ticket_id": ticket_id, "ticket_title": ticket.title,
        "event": "PMS_LOG", "qty": qty_done,
    })
    return _redirect(f"/fms/tickets/{ticket_id}")


@router.post("/pms/{ticket_id}/revise-target")
def pms_revise_target(
    ticket_id: str,
    new_target: int = Form(...),
    revision_reason: str = Form(...),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    """3-A-5: Revise target quantity mid-cycle."""
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    old_target = ticket.target_qty or 0

    db.add(PMSDailyLog(
        ticket_id=ticket_id, tenant_id=user.tenant_id,
        log_date=date.today(), qty_done=0,
        event_type="TARGET_REVISED",
        old_target=old_target, new_target=new_target,
        revision_reason=revision_reason.strip(),
        actor_id=user.id,
    ))
    ticket.target_qty = new_target
    ticket.updated_at = datetime.utcnow()
    _log_event(db, ticket_id, user.id, "COMMENT",
               f"Target revised: {old_target} → {new_target}. Reason: {revision_reason}")
    db.commit()

    # Notify admins + managers
    ids = _admin_manager_ids(db, user.tenant_id)
    broadcast_sync(user.tenant_id, ids, FMS_STAGE_TRANSITION, {
        "ticket_id": ticket_id, "event": "TARGET_REVISED",
        "old": old_target, "new": new_target,
    })
    return _redirect(f"/fms/tickets/{ticket_id}")


# ══════════════════════════════════════════════════════════════════════════════
# 3-B: Dispatch Sub-module
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/dispatch/{ticket_id}", response_class=HTMLResponse)
def dispatch_panel(ticket_id: str, request: Request,
                   user: User = Depends(get_current_user_or_redirect),
                   db: Session = Depends(get_db)):
    """3-B-4: Dispatch panel for a ticket."""
    _require_submodule(db, user.tenant_id, "DISPATCH")
    ticket  = _get_ticket(db, ticket_id, user.tenant_id)
    records = db.query(DispatchRecord).filter(
        DispatchRecord.ticket_id == ticket_id
    ).order_by(DispatchRecord.created_at.desc()).all()

    total_dispatched = sum(r.qty_dispatched for r in records)
    target  = ticket.target_qty or 0
    remaining = max(target - total_dispatched, 0)

    return templates.TemplateResponse(request, "submodules/dispatch_panel.html", _ctx(
        request, user, db,
        ticket=ticket, records=records,
        total_dispatched=total_dispatched,
        target=target, remaining=remaining,
        now=datetime.utcnow(),
    ))


@router.post("/dispatch/{ticket_id}/add")
def dispatch_add(
    ticket_id: str,
    qty_dispatched: int = Form(...),
    unit: str = Form(""),
    vehicle_number: str = Form(""),
    driver_name: str = Form(""),
    destination: str = Form(""),
    expected_delivery: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """3-B-2/3: Add a dispatch record (partial dispatch support)."""
    _require_submodule(db, user.tenant_id, "DISPATCH")
    ticket = _get_ticket(db, ticket_id, user.tenant_id)

    exp_dt = None
    if expected_delivery.strip():
        try:
            exp_dt = datetime.fromisoformat(expected_delivery)
        except ValueError:
            pass

    rec = DispatchRecord(
        ticket_id=ticket_id, tenant_id=user.tenant_id,
        qty_dispatched=qty_dispatched,
        unit=unit.strip() or ticket.qty_unit or "",
        vehicle_number=vehicle_number.strip() or None,
        driver_name=driver_name.strip() or None,
        destination=destination.strip() or None,
        expected_delivery=exp_dt,
        notes=notes.strip() or None,
        actor_id=user.id,
    )
    db.add(rec)
    _log_event(db, ticket_id, user.id, "COMMENT",
               f"Dispatched {qty_dispatched} {rec.unit} to {destination or '?'}")
    db.commit()
    return _redirect(f"/fms/tickets/{ticket_id}")


@router.post("/dispatch/{record_id}/pod")
async def dispatch_pod_upload(
    record_id: str,
    pod_file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """3-B-5: Upload POD — triggers DELIVERED event."""
    rec = db.query(DispatchRecord).get(record_id)
    if not rec or rec.tenant_id != user.tenant_id:
        raise HTTPException(404)

    # Save POD file
    from .uploads import save_upload
    url = await save_upload(pod_file, folder="dispatch_pod")
    rec.proof_photo_url = url
    rec.pod_uploaded_at = datetime.utcnow()
    rec.is_delivered    = True
    rec.delivered_at    = datetime.utcnow()

    _log_event(db, rec.ticket_id, user.id, "COMMENT",
               f"POD uploaded — delivery confirmed for record {record_id}")
    db.commit()
    return _redirect(f"/fms/tickets/{rec.ticket_id}")


# ══════════════════════════════════════════════════════════════════════════════
# 3-C: Invoice Sub-module
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/invoice/{ticket_id}", response_class=HTMLResponse)
def invoice_panel(ticket_id: str, request: Request,
                  user: User = Depends(get_current_user_or_redirect),
                  db: Session = Depends(get_db)):
    """3-C-3/4: Invoice panel with outstanding tracking."""
    _require_submodule(db, user.tenant_id, "INVOICE")
    ticket   = _get_ticket(db, ticket_id, user.tenant_id)
    invoices = db.query(InvoiceRecord).filter(
        InvoiceRecord.ticket_id  == ticket_id,
        InvoiceRecord.is_deleted == False,
    ).order_by(InvoiceRecord.created_at.desc()).all()

    total_invoiced  = sum(i.amount for i in invoices)
    total_received  = sum(i.amount for i in invoices if i.is_paid)
    outstanding     = total_invoiced - total_received
    today = date.today()
    overdue_count   = sum(
        1 for i in invoices
        if not i.is_paid and i.due_date and i.due_date < today)

    return templates.TemplateResponse(request, "submodules/invoice_panel.html", _ctx(
        request, user, db,
        ticket=ticket, invoices=invoices,
        total_invoiced=total_invoiced,
        total_received=total_received,
        outstanding=outstanding,
        overdue_count=overdue_count,
        today=today,
    ))


@router.post("/invoice/{ticket_id}/add")
def invoice_add(
    ticket_id: str,
    invoice_number: str = Form(...),
    amount: float = Form(...),
    currency: str = Form("INR"),
    invoice_date: str = Form(""),
    due_date: str = Form(""),
    payment_terms: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    """3-C-2: Create an invoice record."""
    _require_submodule(db, user.tenant_id, "INVOICE")
    ticket = _get_ticket(db, ticket_id, user.tenant_id)

    inv_date = date.fromisoformat(invoice_date) if invoice_date.strip() else date.today()
    due_dt   = date.fromisoformat(due_date)     if due_date.strip()     else None

    inv = InvoiceRecord(
        ticket_id=ticket_id, tenant_id=user.tenant_id,
        invoice_number=invoice_number.strip(),
        amount=amount, currency=currency.strip() or "INR",
        invoice_date=inv_date, due_date=due_dt,
        payment_terms=payment_terms.strip() or None,
        actor_id=user.id,
    )
    db.add(inv)
    _log_event(db, ticket_id, user.id, "COMMENT",
               f"Invoice {invoice_number} added: {currency} {amount:.2f}")
    db.commit()
    return _redirect(f"/fms/tickets/{ticket_id}")


@router.post("/invoice/{invoice_id}/mark-paid")
def invoice_mark_paid(
    invoice_id: str,
    payment_ref: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    """3-C-3: Mark invoice as paid."""
    inv = db.query(InvoiceRecord).get(invoice_id)
    if not inv or inv.tenant_id != user.tenant_id:
        raise HTTPException(404)
    inv.is_paid     = True
    inv.paid_at     = datetime.utcnow()
    inv.payment_ref = payment_ref.strip() or None
    inv.updated_at  = datetime.utcnow()
    _log_event(db, inv.ticket_id, user.id, "COMMENT",
               f"Invoice {inv.invoice_number} marked PAID. Ref: {payment_ref}")
    db.commit()
    return _redirect(f"/fms/tickets/{inv.ticket_id}")


# ══════════════════════════════════════════════════════════════════════════════
# 3-E: Custom Sub-module Renderer
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/custom/{ticket_id}/{stage_id}", response_class=HTMLResponse)
def custom_panel(ticket_id: str, stage_id: str, request: Request,
                 user: User = Depends(get_current_user_or_redirect),
                 db: Session = Depends(get_db)):
    """3-E-2/3: Render custom sub-module form from library definition."""
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    stage  = db.query(FMSStage).filter(
        FMSStage.id == stage_id, FMSStage.flow_id == ticket.flow_id).first()
    if not stage or not stage.deployed_submodule_id:
        raise HTTPException(404, "No custom sub-module configured for this stage")

    submodule_def = stage.deployed_submodule

    # Find existing response for this ticket+stage
    existing = db.query(CustomSubmoduleResponse).filter(
        CustomSubmoduleResponse.ticket_id == ticket_id,
        CustomSubmoduleResponse.stage_id  == stage_id,
    ).first()

    fields = _json.loads(submodule_def.fields_json) if submodule_def.fields_json else []
    responses = _json.loads(existing.field_responses_json) if existing else {}
    is_complete = existing.is_complete if existing else False

    return templates.TemplateResponse(request, "submodules/custom_panel.html", _ctx(
        request, user, db,
        ticket=ticket, stage=stage,
        submodule_def=submodule_def, fields=fields,
        responses=responses, is_complete=is_complete,
        existing=existing,
    ))


@router.post("/custom/{ticket_id}/{stage_id}/submit")
async def custom_submit(
    ticket_id: str, stage_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """3-E-2: Save custom sub-module response."""
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    stage  = db.query(FMSStage).filter(
        FMSStage.id == stage_id, FMSStage.flow_id == ticket.flow_id).first()
    if not stage or not stage.deployed_submodule_id:
        raise HTTPException(404)

    # Parse form data
    form_data = await request.form()
    submodule_def = stage.deployed_submodule
    fields = _json.loads(submodule_def.fields_json) if submodule_def.fields_json else []

    responses = {}
    for field in fields:
        fid = field.get("id", "")
        val = form_data.get(f"field_{fid}", "")
        responses[fid] = str(val)

    is_complete = form_data.get("mark_complete") == "true"

    existing = db.query(CustomSubmoduleResponse).filter(
        CustomSubmoduleResponse.ticket_id == ticket_id,
        CustomSubmoduleResponse.stage_id  == stage_id,
    ).first()

    if existing:
        existing.field_responses_json = _json.dumps(responses)
        existing.is_complete          = is_complete
        existing.submitted_at         = datetime.utcnow()
        existing.actor_id             = user.id
        existing.updated_at           = datetime.utcnow()
    else:
        db.add(CustomSubmoduleResponse(
            ticket_id=ticket_id, stage_id=stage_id,
            tenant_id=user.tenant_id,
            submodule_def_id=stage.deployed_submodule_id,
            field_responses_json=_json.dumps(responses),
            is_complete=is_complete,
            submitted_at=datetime.utcnow() if is_complete else None,
            actor_id=user.id,
        ))

    if is_complete:
        _log_event(db, ticket_id, user.id, "STAGE_EXITED",
                   f"Custom sub-module '{submodule_def.name}' completed")

    db.commit()
    return _redirect(f"/fms/tickets/{ticket_id}")


# ══════════════════════════════════════════════════════════════════════════════
# 3-F: JSON data API — used by ticket detail modal popup
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/data/{ticket_id}/{sub_module_tag}")
def submodule_data_api(
    ticket_id: str,
    sub_module_tag: str,
    stage_id: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return submodule data as JSON for the inline modal on ticket_detail."""
    ticket = _get_ticket(db, ticket_id, user.tenant_id)
    tag = sub_module_tag.upper()

    if tag == "PMS":
        logs = db.query(PMSDailyLog).filter(
            PMSDailyLog.ticket_id == ticket_id
        ).order_by(PMSDailyLog.log_date.asc()).all()
        total_done = sum(l.qty_done for l in logs if l.event_type == "DAILY_LOG")
        entries = []
        for l in logs:
            actor = db.query(User).get(l.actor_id) if l.actor_id else None
            entries.append({
                "date": l.log_date.isoformat() if l.log_date else None,
                "event_type": l.event_type,
                "qty_done": l.qty_done,
                "has_blockers": l.has_blockers,
                "comment": l.comment,
                "actor": actor.name if actor else None,
                "old_target": getattr(l, "old_target", None),
                "new_target": getattr(l, "new_target", None),
                "revision_reason": getattr(l, "revision_reason", None),
            })
        return JSONResponse({"type": "PMS", "total_done": total_done,
                             "target": ticket.target_qty or 0,
                             "unit": ticket.qty_unit or "units",
                             "entries": entries})

    if tag == "DISPATCH":
        records = db.query(DispatchRecord).filter(
            DispatchRecord.ticket_id == ticket_id
        ).order_by(DispatchRecord.created_at.asc()).all()
        total_dispatched = sum(r.qty_dispatched for r in records)
        rows = []
        for r in records:
            actor = db.query(User).get(r.actor_id) if r.actor_id else None
            rows.append({
                "id": r.id,
                "qty_dispatched": r.qty_dispatched,
                "unit": r.unit,
                "vehicle_number": r.vehicle_number,
                "driver_name": r.driver_name,
                "destination": r.destination,
                "expected_delivery": r.expected_delivery.isoformat() if r.expected_delivery else None,
                "pod_uploaded": bool(r.proof_photo_url),
                "notes": r.notes,
                "actor": actor.name if actor else None,
                "created_at": r.created_at.strftime("%d %b %Y, %H:%M") if r.created_at else None,
            })
        return JSONResponse({"type": "DISPATCH", "total_dispatched": total_dispatched,
                             "target": ticket.target_qty or 0,
                             "unit": ticket.qty_unit or "units",
                             "records": rows})

    if tag == "INVOICE":
        invoices = db.query(InvoiceRecord).filter(
            InvoiceRecord.ticket_id == ticket_id,
            InvoiceRecord.is_deleted == False,
        ).order_by(InvoiceRecord.created_at.asc()).all()
        total_invoiced = sum(i.amount for i in invoices)
        total_received = sum(i.amount for i in invoices if i.is_paid)
        rows = []
        for i in invoices:
            actor = db.query(User).get(i.actor_id) if i.actor_id else None
            rows.append({
                "id": i.id,
                "invoice_number": i.invoice_number,
                "amount": i.amount,
                "currency": i.currency,
                "invoice_date": i.invoice_date.isoformat() if i.invoice_date else None,
                "due_date": i.due_date.isoformat() if i.due_date else None,
                "is_paid": i.is_paid,
                "paid_at": i.paid_at.strftime("%d %b %Y") if i.paid_at else None,
                "payment_ref": getattr(i, "payment_ref", None),
                "payment_terms": i.payment_terms,
                "actor": actor.name if actor else None,
                "created_at": i.created_at.strftime("%d %b %Y") if i.created_at else None,
            })
        return JSONResponse({"type": "INVOICE",
                             "total_invoiced": total_invoiced,
                             "total_received": total_received,
                             "outstanding": total_invoiced - total_received,
                             "invoices": rows})

    if tag == "CUSTOM" and stage_id:
        stage = db.query(FMSStage).filter(
            FMSStage.id == stage_id, FMSStage.flow_id == ticket.flow_id).first()
        existing = db.query(CustomSubmoduleResponse).filter(
            CustomSubmoduleResponse.ticket_id == ticket_id,
            CustomSubmoduleResponse.stage_id  == stage_id,
        ).first() if stage else None
        submodule_def = stage.deployed_submodule if stage and stage.deployed_submodule_id else None
        fields = _json.loads(submodule_def.fields_json) if submodule_def and submodule_def.fields_json else []
        responses = _json.loads(existing.field_responses_json) if existing else {}
        return JSONResponse({"type": "CUSTOM",
                             "name": submodule_def.name if submodule_def else "Custom",
                             "is_complete": existing.is_complete if existing else False,
                             "fields": fields, "responses": responses})

    raise HTTPException(404, "Unknown sub-module type")


# ══════════════════════════════════════════════════════════════════════════════
# 3-E-4: Deploy sub-module to a stage (Admin)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/deploy-to-stage")
def deploy_submodule_to_stage(
    stage_id: str = Form(...),
    submodule_def_id: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """3-E-4: Attach a library sub-module definition to an FMS stage."""
    stage = db.query(FMSStage).filter(
        FMSStage.id == stage_id,
        FMSStage.tenant_id == user.tenant_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")

    submod = db.query(LibrarySubmoduleDefinition).get(submodule_def_id)
    if not submod:
        raise HTTPException(404, "Sub-module definition not found")

    stage.sub_module_tag        = "CUSTOM"
    stage.deployed_submodule_id = submodule_def_id
    db.commit()
    return _redirect(f"/fms/flows/{stage.flow_id}?msg=submodule_deployed")