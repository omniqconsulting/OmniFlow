"""
Attendance & Leave — Workstream B (B1-B4).

Employee geo-tagged punch in/out with geofencing (B1-B2), manager/admin
team log with out-of-fence visibility (B3), and a manual half-day override
field plus day-status helper laid as structure only for future payroll
(B4 — no payroll/salary calculation logic here). Leave apply/approve/reject
rounds out the module. Gated tenant-wide behind the ATTENDANCE feature flag.
"""
import math
from datetime import datetime, date as _date

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .database import (
    get_db, Tenant, User, Branch,
    AttendanceRecord, AttendanceGeofence, LeaveRequest,
)
from .auth import (
    get_current_user_or_redirect, require_manager_or_redirect, require_admin_or_redirect,
    get_nav_flags, require_module,
)
from .uploads import save_upload
from .templates_env import templates
from .labels import get_labels, DEFAULT_L

router = APIRouter(prefix="/attendance", tags=["Attendance"])

require_attendance = require_module("ATTENDANCE", "ATTENDANCE", redirect_unauthenticated=True)


def _L(db, user):
    if user is None:
        return DEFAULT_L
    return get_labels(db, user.tenant_id)


def _ctx(request, user, db, **kw):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first() if user else None
    return {
        "request": request, "user": user,
        "L": _L(db, user),
        **get_nav_flags(db, user, tenant),
        **kw,
    }


# ── Geo helpers ───────────────────────────────────────────────────────────────

def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance between two lat/lng points, in meters."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(min(1, math.sqrt(a)))


def _resolve_geofence(db: Session, tenant_id: str, branch_id: str):
    """Branch-specific geofence if configured, else the tenant-wide default
    (branch_id IS NULL), else None (no geofence configured for this tenant)."""
    fence = None
    if branch_id:
        fence = db.query(AttendanceGeofence).filter(
            AttendanceGeofence.tenant_id == tenant_id,
            AttendanceGeofence.branch_id == branch_id,
            AttendanceGeofence.is_active == True,
        ).first()
    if not fence:
        fence = db.query(AttendanceGeofence).filter(
            AttendanceGeofence.tenant_id == tenant_id,
            AttendanceGeofence.branch_id.is_(None),
            AttendanceGeofence.is_active == True,
        ).first()
    return fence


def _check_in_fence(fence, lat, lng):
    """Returns (in_fence: bool). No geofence configured => always in-fence,
    so employees are never blocked because Setup was never configured."""
    if not fence:
        return True
    dist = _haversine_m(lat, lng, fence.center_lat, fence.center_lng)
    return dist <= fence.radius_m


# ── Day-status helper (shared with my_tasks.py) ─────────────────────────────

def _day_status(db, record, approved_leave_dates_set, today=None) -> str:
    """PRESENT / HALF_DAY / ON_LEAVE / ABSENT / FUTURE. No auto-calculation
    from durations/thresholds — B4 is structure only, no payroll logic."""
    today = today or _date.today()
    work_date = record.work_date if record else None
    if work_date and work_date > today:
        return "FUTURE"
    if work_date and work_date in approved_leave_dates_set:
        return "ON_LEAVE"
    if record and record.is_half_day:
        return "HALF_DAY"
    if record and record.check_in_at:
        return "PRESENT"
    return "ABSENT"


# ── Scope helper (mirrors analytics.py::_resolve_filter_uids pattern) ──────

def _scoped_user_ids(db: Session, user: User) -> list:
    """MANAGER sees direct reports only; ADMIN sees the whole tenant."""
    if user.role == "ADMIN":
        return [u.id for u in db.query(User).filter(
            User.tenant_id == user.tenant_id, User.is_deleted == False).all()]
    return [u.id for u in db.query(User).filter(
        User.tenant_id == user.tenant_id, User.is_deleted == False,
        User.manager_id == user.id).all()]


# ── a) Punch page ────────────────────────────────────────────────────────────

@router.get("/punch", response_class=HTMLResponse)
def punch_page(request: Request, user: User = Depends(get_current_user_or_redirect),
                db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    from .constants import has_feature
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    today = _date.today()
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.user_id == user.id, AttendanceRecord.work_date == today).first()
    fence = _resolve_geofence(db, user.tenant_id, user.branch_id)

    return templates.TemplateResponse(request, "attendance_punch.html", _ctx(
        request, user, db,
        record=record, today=today,
        fence_lat=fence.center_lat if fence else None,
        fence_lng=fence.center_lng if fence else None,
        fence_radius=fence.radius_m if fence else None,
        has_fence=bool(fence),
        now=datetime.utcnow(),
    ))


@router.post("/punch/checkin")
async def punch_checkin(request: Request,
                         lat: float = Form(...), lng: float = Form(...),
                         reason: str = Form(None),
                         photo: UploadFile = File(...),
                         user: User = Depends(get_current_user_or_redirect),
                         db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    today = _date.today()
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.user_id == user.id, AttendanceRecord.work_date == today).first()
    if record and record.check_in_at:
        raise HTTPException(status_code=400, detail="Already checked in today")

    fence = _resolve_geofence(db, user.tenant_id, user.branch_id)
    in_fence = _check_in_fence(fence, lat, lng)
    reason = (reason or "").strip() or None
    if not in_fence and not reason:
        raise HTTPException(status_code=400, detail="You're outside the office zone — please add a reason to check in.")

    upload = await save_upload(photo, user.tenant_id)

    if not record:
        record = AttendanceRecord(tenant_id=user.tenant_id, user_id=user.id, work_date=today)
        db.add(record)

    record.check_in_at = datetime.utcnow()
    record.check_in_lat = lat
    record.check_in_lng = lng
    record.check_in_in_fence = in_fence
    record.check_in_reason = reason
    record.photo_path = upload.get("file_path")
    db.commit()

    if request.headers.get("accept", "").find("application/json") >= 0 or request.headers.get("x-requested-with"):
        return JSONResponse({"ok": True, "check_in_at": record.check_in_at.isoformat(), "in_fence": in_fence})
    return RedirectResponse("/attendance/punch", status_code=303)


@router.post("/punch/checkout")
async def punch_checkout(request: Request,
                          lat: float = Form(...), lng: float = Form(...),
                          reason: str = Form(None),
                          user: User = Depends(get_current_user_or_redirect),
                          db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    today = _date.today()
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.user_id == user.id, AttendanceRecord.work_date == today).first()
    if not record or not record.check_in_at:
        raise HTTPException(status_code=400, detail="Check in first")
    if record.check_out_at:
        raise HTTPException(status_code=400, detail="Already checked out today")

    fence = _resolve_geofence(db, user.tenant_id, user.branch_id)
    in_fence = _check_in_fence(fence, lat, lng)
    reason = (reason or "").strip() or None
    if not in_fence and not reason:
        raise HTTPException(status_code=400, detail="You're outside the office zone — please add a reason to check out.")

    record.check_out_at = datetime.utcnow()
    record.check_out_lat = lat
    record.check_out_lng = lng
    record.check_out_in_fence = in_fence
    record.check_out_reason = reason
    db.commit()

    if request.headers.get("accept", "").find("application/json") >= 0 or request.headers.get("x-requested-with"):
        return JSONResponse({"ok": True, "check_out_at": record.check_out_at.isoformat(), "in_fence": in_fence})
    return RedirectResponse("/attendance/punch", status_code=303)


# ── d) Manager/Admin team log ────────────────────────────────────────────────

@router.get("/team", response_class=HTMLResponse)
def team_log(request: Request, day: str = "",
             user: User = Depends(require_manager_or_redirect),
             db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    try:
        sel_date = _date.fromisoformat(day) if day else _date.today()
    except ValueError:
        sel_date = _date.today()

    uids = _scoped_user_ids(db, user)
    users_by_id = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}

    records = db.query(AttendanceRecord).filter(
        AttendanceRecord.tenant_id == user.tenant_id,
        AttendanceRecord.user_id.in_(uids),
        AttendanceRecord.work_date == sel_date,
    ).all() if uids else []
    records_by_uid = {r.user_id: r for r in records}

    approved_leaves = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == user.tenant_id,
        LeaveRequest.user_id.in_(uids),
        LeaveRequest.status == "APPROVED",
        LeaveRequest.start_date <= sel_date,
        LeaveRequest.end_date >= sel_date,
    ).all() if uids else []
    leave_uids = {l.user_id for l in approved_leaves}

    rows = []
    out_of_fence_count = 0
    for uid in uids:
        u = users_by_id.get(uid)
        if not u:
            continue
        rec = records_by_uid.get(uid)
        status = _day_status(db, rec, {sel_date} if uid in leave_uids else set(), sel_date)
        oof = bool(rec and (rec.check_in_in_fence is False or rec.check_out_in_fence is False))
        if oof:
            out_of_fence_count += 1
        rows.append({"user": u, "record": rec, "status": status, "out_of_fence": oof})

    rows.sort(key=lambda r: r["user"].name)

    return templates.TemplateResponse(request, "attendance_team.html", _ctx(
        request, user, db,
        rows=rows, sel_date=sel_date, out_of_fence_count=out_of_fence_count,
    ))


@router.post("/team/records/{record_id}/toggle-half-day")
def toggle_half_day(record_id: str, user: User = Depends(require_manager_or_redirect),
                     db: Session = Depends(get_db)):
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.id == record_id, AttendanceRecord.tenant_id == user.tenant_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    record.is_half_day = not record.is_half_day
    db.commit()
    return RedirectResponse("/attendance/team", status_code=303)


# ── e/f/g) Leave ─────────────────────────────────────────────────────────────

@router.get("/leave", response_class=HTMLResponse)
def leave_page(request: Request, user: User = Depends(get_current_user_or_redirect),
                db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    my_requests = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == user.tenant_id, LeaveRequest.user_id == user.id
    ).order_by(LeaveRequest.created_at.desc()).all()

    pending_queue = []
    if user.role in ("ADMIN", "MANAGER"):
        uids = _scoped_user_ids(db, user)
        users_by_id = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
        pending = db.query(LeaveRequest).filter(
            LeaveRequest.tenant_id == user.tenant_id,
            LeaveRequest.user_id.in_(uids),
            LeaveRequest.status == "PENDING",
        ).order_by(LeaveRequest.created_at.asc()).all() if uids else []
        for p in pending:
            pending_queue.append({"req": p, "user": users_by_id.get(p.user_id)})

    today = _date.today()
    on_leave_today = bool(db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == user.tenant_id, LeaveRequest.user_id == user.id,
        LeaveRequest.status == "APPROVED",
        LeaveRequest.start_date <= today, LeaveRequest.end_date >= today,
    ).first())

    return templates.TemplateResponse(request, "attendance_leave.html", _ctx(
        request, user, db,
        my_requests=my_requests, pending_queue=pending_queue,
        on_leave_today=on_leave_today, today=today,
    ))


@router.post("/leave/apply")
def leave_apply(leave_type: str = Form("CASUAL"),
                 start_date: str = Form(...), end_date: str = Form(...),
                 is_half_day: bool = Form(False), reason: str = Form(None),
                 user: User = Depends(get_current_user_or_redirect),
                 db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    try:
        sd = _date.fromisoformat(start_date)
        ed = _date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    if ed < sd:
        raise HTTPException(status_code=400, detail="End date must be on or after start date")

    req = LeaveRequest(
        tenant_id=user.tenant_id, user_id=user.id, leave_type=leave_type,
        start_date=sd, end_date=ed, is_half_day=bool(is_half_day) and sd == ed,
        reason=(reason or "").strip() or None, status="PENDING",
    )
    db.add(req)
    db.commit()
    return RedirectResponse("/attendance/leave", status_code=303)


def _authorize_decision(db, user, req):
    if user.role == "ADMIN":
        return True
    if user.role == "MANAGER":
        target = db.query(User).filter(User.id == req.user_id).first()
        return bool(target and target.manager_id == user.id)
    return False


@router.post("/leave/{req_id}/approve")
def leave_approve(req_id: str, user: User = Depends(require_manager_or_redirect),
                   db: Session = Depends(get_db)):
    req = db.query(LeaveRequest).filter(
        LeaveRequest.id == req_id, LeaveRequest.tenant_id == user.tenant_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if not _authorize_decision(db, user, req):
        raise HTTPException(status_code=403, detail="Not authorized to decide this request")
    req.status = "APPROVED"
    req.approver_id = user.id
    req.decided_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/attendance/leave", status_code=303)


@router.post("/leave/{req_id}/reject")
def leave_reject(req_id: str, decision_note: str = Form(None),
                  user: User = Depends(require_manager_or_redirect),
                  db: Session = Depends(get_db)):
    req = db.query(LeaveRequest).filter(
        LeaveRequest.id == req_id, LeaveRequest.tenant_id == user.tenant_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if not _authorize_decision(db, user, req):
        raise HTTPException(status_code=403, detail="Not authorized to decide this request")
    req.status = "REJECTED"
    req.approver_id = user.id
    req.decided_at = datetime.utcnow()
    req.decision_note = (decision_note or "").strip() or None
    db.commit()
    return RedirectResponse("/attendance/leave", status_code=303)


# ── h) Admin geofence setup ──────────────────────────────────────────────────

@router.get("/setup/geofence", response_class=HTMLResponse)
def geofence_setup(request: Request, user: User = Depends(require_admin_or_redirect),
                    db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    fences = db.query(AttendanceGeofence).filter(
        AttendanceGeofence.tenant_id == user.tenant_id).order_by(AttendanceGeofence.created_at.desc()).all()
    branches = db.query(Branch).filter(
        Branch.tenant_id == user.tenant_id, Branch.is_deleted == False).order_by(Branch.name).all()

    return templates.TemplateResponse(request, "attendance_geofence_setup.html", _ctx(
        request, user, db, fences=fences, branches=branches,
    ))


@router.post("/setup/geofence")
def geofence_create(site_name: str = Form("Main Office"),
                     branch_id: str = Form(None),
                     center_lat: float = Form(...), center_lng: float = Form(...),
                     radius_m: int = Form(200),
                     user: User = Depends(require_admin_or_redirect),
                     db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    fence = AttendanceGeofence(
        tenant_id=user.tenant_id, branch_id=(branch_id or None),
        site_name=site_name or "Main Office",
        center_lat=center_lat, center_lng=center_lng, radius_m=radius_m or 200,
    )
    db.add(fence)
    db.commit()
    return RedirectResponse("/attendance/setup/geofence", status_code=303)


@router.post("/setup/geofence/{fence_id}/delete")
def geofence_delete(fence_id: str, user: User = Depends(require_admin_or_redirect),
                     db: Session = Depends(get_db)):
    fence = db.query(AttendanceGeofence).filter(
        AttendanceGeofence.id == fence_id, AttendanceGeofence.tenant_id == user.tenant_id).first()
    if fence:
        db.delete(fence)
        db.commit()
    return RedirectResponse("/attendance/setup/geofence", status_code=303)
