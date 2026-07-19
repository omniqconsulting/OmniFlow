"""
Attendance & Leave — Workstream B (B1-B4).

Employee geo-tagged punch in/out with geofencing (B1-B2), manager/admin
team log with out-of-fence visibility (B3), and a manual half-day override
field plus day-status helper laid as structure only for future payroll
(B4 — no payroll/salary calculation logic here). Leave apply/approve/reject
rounds out the module. Gated tenant-wide behind the ATTENDANCE feature flag.
"""
import json
import math
from datetime import datetime, date as _date

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .database import (
    get_db, Tenant, User, Branch, Department,
    AttendanceRecord, AttendanceGeofence, LeaveRequest, AttendanceRule,
)
from .auth import (
    get_current_user_or_redirect, require_manager_or_redirect, require_admin_or_redirect,
    get_nav_flags, require_module,
)
from .uploads import save_upload
from .templates_env import templates
from .labels import get_labels, DEFAULT_L
from .attendance_rules import evaluate_attendance_rules

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


# ── Weekly-off helper (branch-level, client feedback #5) ───────────────────

def _branch_weekly_off_days(db, branch_id) -> set:
    """Set of date.weekday() ints (0=Mon..6=Sun) that are non-working days
    for this branch. No branch_id => empty set (every day is a working day,
    never errors)."""
    if not branch_id:
        return set()
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch or not branch.weekly_off_days:
        return set()
    try:
        return set(json.loads(branch.weekly_off_days))
    except (TypeError, ValueError):
        return set()


def is_working_day(db, user, work_date=None) -> bool:
    """False on the employee's branch weekly-off day. Used to gate the
    punch page / My Tasks red-urgency framing."""
    work_date = work_date or _date.today()
    off_days = _branch_weekly_off_days(db, getattr(user, "branch_id", None))
    return work_date.weekday() not in off_days


# ── Day-status helper (shared with my_tasks.py, /attendance/team, /attendance/report) ──

def _day_status(db, record, approved_leave_dates_set, eval_date=None, employee=None) -> str:
    """PRESENT / HALF_DAY / ON_LEAVE / ABSENT / WEEKLY_OFF / FUTURE.

    Precedence (client-confirmed, section 6): FUTURE > WEEKLY_OFF > ON_LEAVE >
    manual is_half_day toggle (human override, short-circuits rule eval) >
    rule engine result > mechanical fallback (PRESENT if checked in, else
    ABSENT). No payroll/salary calculation — status only (B4).

    eval_date is the calendar day being evaluated (a report/grid column, or
    the selected team-view date) — NOT necessarily today's real date. When
    there's no punch record at all for that day, work_date must still fall
    back to eval_date, otherwise weekly-off/leave/future checks silently
    no-op for any un-punched day (the bug this docstring replaced)."""
    eval_date = eval_date or _date.today()
    work_date = record.work_date if record else eval_date
    if work_date > _date.today():
        return "FUTURE"

    if employee is not None and work_date:
        off_days = _branch_weekly_off_days(db, getattr(employee, "branch_id", None))
        if work_date.weekday() in off_days:
            return "WEEKLY_OFF"

    if work_date and work_date in approved_leave_dates_set:
        return "ON_LEAVE"

    if record and record.is_half_day:
        return "HALF_DAY"

    if record is not None:
        tenant_id = getattr(record, "tenant_id", None) or (employee.tenant_id if employee else None)
        if tenant_id:
            rule_outcome = evaluate_attendance_rules(db, tenant_id, record)
            if rule_outcome in ("PRESENT", "HALF_DAY", "ABSENT"):
                return rule_outcome

    if record and record.check_in_at:
        return "PRESENT"
    return "ABSENT"


def get_self_month_calendar(db, employee, year=None, month=None) -> dict:
    """One employee's own current/selected month, day-by-day — the same
    day-status logic as /attendance/report's grid, scoped to a single user.
    Used by My Tasks' Attendance tab so an EMPLOYEE (who can't reach
    /organization or /attendance/report, both manager/admin-gated) still
    gets a calendar view of their own attendance."""
    import calendar as _cal
    today = _date.today()
    year = year or today.year
    month = month or today.month
    days_in_month = _cal.monthrange(year, month)[1]
    day_list = [_date(year, month, d) for d in range(1, days_in_month + 1)]

    records = db.query(AttendanceRecord).filter(
        AttendanceRecord.tenant_id == employee.tenant_id,
        AttendanceRecord.user_id == employee.id,
        AttendanceRecord.work_date >= day_list[0],
        AttendanceRecord.work_date <= day_list[-1],
    ).all()
    records_by_date = {r.work_date: r for r in records}

    leaves = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == employee.tenant_id,
        LeaveRequest.user_id == employee.id,
        LeaveRequest.status == "APPROVED",
        LeaveRequest.start_date <= day_list[-1],
        LeaveRequest.end_date >= day_list[0],
    ).all()
    leave_dates = set()
    for lv in leaves:
        s, e = max(lv.start_date, day_list[0]), min(lv.end_date, day_list[-1])
        d = s
        while d <= e:
            leave_dates.add(d)
            d = _date.fromordinal(d.toordinal() + 1)

    days = []
    for d in day_list:
        rec = records_by_date.get(d)
        status = _day_status(db, rec, leave_dates, d, employee=employee)
        days.append({"date": d, "status": status, "record": rec})

    return {
        "year": year, "month": month,
        "month_name": _cal.month_name[month],
        "days": days,
    }


# ── Scope helper (mirrors analytics.py::_resolve_filter_uids pattern) ──────

def _scoped_user_ids(db: Session, user: User) -> list:
    """MANAGER sees direct reports only; ADMIN sees the whole tenant.
    Deduplicated (dict.fromkeys preserves order) — defensive against a
    tenant having two employee rows for the same person, which would
    otherwise surface as that employee appearing twice in the team/report
    views even though neither query itself joins or fans out rows."""
    if user.role == "ADMIN":
        ids = [u.id for u in db.query(User).filter(
            User.tenant_id == user.tenant_id, User.is_deleted == False).all()]
    else:
        ids = [u.id for u in db.query(User).filter(
            User.tenant_id == user.tenant_id, User.is_deleted == False,
            User.manager_id == user.id).all()]
    return list(dict.fromkeys(ids))


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

    # Branch used for today's geofence: the one-time override chosen at
    # check-in (record.branch_id) once punched in; otherwise the employee's
    # own default branch, changeable pre-check-in via the branch picker.
    effective_branch_id = (record.branch_id if record and record.branch_id else None) or user.branch_id
    fence = _resolve_geofence(db, user.tenant_id, effective_branch_id)

    default_branch = db.query(Branch).filter(Branch.id == user.branch_id).first() if user.branch_id else None
    all_branches = db.query(Branch).filter(
        Branch.tenant_id == user.tenant_id, Branch.is_deleted == False,
    ).order_by(Branch.name).all()

    # Persisted post-submission distance-from-office, computed once here from
    # the record's own stored lat/lng rather than recomputed client-side, so
    # re-visiting the page still shows the geo-confirmation line.
    checkin_distance_m = None
    if record and fence and record.check_in_lat is not None:
        checkin_distance_m = round(_haversine_m(record.check_in_lat, record.check_in_lng, fence.center_lat, fence.center_lng))
    checkout_distance_m = None
    if record and fence and record.check_out_lat is not None:
        checkout_distance_m = round(_haversine_m(record.check_out_lat, record.check_out_lng, fence.center_lat, fence.center_lng))

    return templates.TemplateResponse(request, "attendance_punch.html", _ctx(
        request, user, db,
        record=record, today=today,
        fence_lat=fence.center_lat if fence else None,
        fence_lng=fence.center_lng if fence else None,
        fence_radius=fence.radius_m if fence else None,
        has_fence=bool(fence),
        now=datetime.utcnow(),
        is_working_day=is_working_day(db, user, today),
        checkin_distance_m=checkin_distance_m,
        checkout_distance_m=checkout_distance_m,
        default_branch=default_branch,
        all_branches=all_branches,
        my_month=get_self_month_calendar(db, user),
    ))


@router.post("/punch/checkin")
async def punch_checkin(request: Request,
                         lat: float = Form(...), lng: float = Form(...),
                         reason: str = Form(None),
                         branch_id: str = Form(None),
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

    # One-time branch override for today's punch only (client's #6.2) — must
    # belong to the same tenant, otherwise silently fall back to the
    # employee's own default branch.
    effective_branch_id = user.branch_id
    if branch_id:
        picked = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id).first()
        if picked:
            effective_branch_id = picked.id

    fence = _resolve_geofence(db, user.tenant_id, effective_branch_id)
    in_fence = _check_in_fence(fence, lat, lng)
    reason = (reason or "").strip() or None
    if not in_fence and not reason:
        raise HTTPException(status_code=400, detail="You're outside the office zone — please add a reason to check in.")

    upload = await save_upload(photo, user.tenant_id)

    if not record:
        record = AttendanceRecord(tenant_id=user.tenant_id, user_id=user.id, work_date=today)
        db.add(record)

    record.branch_id = effective_branch_id
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

    # Validate checkout against the same branch chosen at check-in, not
    # necessarily the employee's default branch.
    fence = _resolve_geofence(db, user.tenant_id, record.branch_id or user.branch_id)
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
        status = _day_status(db, rec, {sel_date} if uid in leave_uids else set(), sel_date, employee=u)
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


# ── i) Organization attendance report — client's #2 ─────────────────────────

import calendar as _calendar


@router.get("/report", response_class=HTMLResponse)
def attendance_report(request: Request,
                       month: int = 0, year: int = 0,
                       department_id: str = "", branch_id: str = "", employee_id: str = "",
                       user: User = Depends(require_manager_or_redirect),
                       db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    today = _date.today()
    year = year or today.year
    month = month or today.month
    days_in_month = _calendar.monthrange(year, month)[1]
    day_list = [_date(year, month, d) for d in range(1, days_in_month + 1)]

    uids = _scoped_user_ids(db, user)
    q = db.query(User).filter(User.id.in_(uids)) if uids else db.query(User).filter(False)
    if department_id:
        q = q.filter(User.department_id == department_id)
    if branch_id:
        q = q.filter(User.branch_id == branch_id)
    if employee_id:
        q = q.filter(User.id == employee_id)
    employees = q.order_by(User.name).all()
    emp_ids = [e.id for e in employees]

    records = db.query(AttendanceRecord).filter(
        AttendanceRecord.tenant_id == user.tenant_id,
        AttendanceRecord.user_id.in_(emp_ids),
        AttendanceRecord.work_date >= day_list[0],
        AttendanceRecord.work_date <= day_list[-1],
    ).all() if emp_ids else []
    records_by_key = {(r.user_id, r.work_date): r for r in records}

    leaves = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == user.tenant_id,
        LeaveRequest.user_id.in_(emp_ids),
        LeaveRequest.status == "APPROVED",
        LeaveRequest.start_date <= day_list[-1],
        LeaveRequest.end_date >= day_list[0],
    ).all() if emp_ids else []
    leave_dates_by_uid = {}
    for lv in leaves:
        s = max(lv.start_date, day_list[0])
        e = min(lv.end_date, day_list[-1])
        d = s
        dates = leave_dates_by_uid.setdefault(lv.user_id, set())
        while d <= e:
            dates.add(d)
            d = _date.fromordinal(d.toordinal() + 1)

    grid = []
    for emp in employees:
        row_days = []
        for d in day_list:
            rec = records_by_key.get((emp.id, d))
            status = _day_status(db, rec, leave_dates_by_uid.get(emp.id, set()), d, employee=emp)
            row_days.append({"date": d, "status": status, "record": rec})
        grid.append({"user": emp, "days": row_days})

    departments = db.query(Department).filter(
        Department.tenant_id == user.tenant_id, Department.is_deleted == False).order_by(Department.name).all()
    branches = db.query(Branch).filter(
        Branch.tenant_id == user.tenant_id, Branch.is_deleted == False).order_by(Branch.name).all()

    detail_employee = None
    detail_days = None
    if employee_id and len(grid) == 1:
        detail_employee = grid[0]["user"]
        detail_days = grid[0]["days"]

    return templates.TemplateResponse(request, "attendance_report.html", _ctx(
        request, user, db,
        grid=grid, day_list=day_list, month=month, year=year,
        departments=departments, branches=branches, employees_all=employees,
        selected_department=department_id, selected_branch=branch_id, selected_employee=employee_id,
        detail_employee=detail_employee, detail_days=detail_days,
        month_name=_calendar.month_name[month],
    ))


# ── j) Admin attendance rule-engine setup — client's #6 ─────────────────────

RULE_OUTCOMES = ("PRESENT", "HALF_DAY", "ABSENT")


@router.get("/setup/rules", response_class=HTMLResponse)
def rules_setup(request: Request, user: User = Depends(require_admin_or_redirect),
                 db: Session = Depends(get_db)):
    from .constants import has_feature
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not has_feature(tenant, "ATTENDANCE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")

    from .attendance_rules import FIELD_CATALOG, OPERATORS_BY_KIND
    rules = db.query(AttendanceRule).filter(
        AttendanceRule.tenant_id == user.tenant_id).order_by(AttendanceRule.priority.asc()).all()
    rules_view = []
    for r in rules:
        try:
            conds = json.loads(r.conditions_json or "[]")
        except (TypeError, ValueError):
            conds = []
        rules_view.append({"rule": r, "conditions": conds})

    field_kinds = {f: kind for f, (kind, _fn) in FIELD_CATALOG.items()}

    return templates.TemplateResponse(request, "setup/attendance_rules.html", _ctx(
        request, user, db,
        rules_view=rules_view, fields=list(FIELD_CATALOG.keys()),
        operators_by_kind=OPERATORS_BY_KIND, field_kinds=field_kinds,
        outcomes=RULE_OUTCOMES,
    ))


def _collect_conditions_from_form(form) -> list:
    conditions = []
    for i in range(1, 4):
        field = (form.get(f"field_{i}") or "").strip()
        operator = (form.get(f"operator_{i}") or "").strip()
        value = (form.get(f"value_{i}") or "").strip()
        if field and operator:
            conditions.append({"field": field, "operator": operator, "value": value})
    return conditions


@router.post("/setup/rules")
async def rules_create(request: Request,
                        user: User = Depends(require_admin_or_redirect),
                        db: Session = Depends(get_db)):
    form = await request.form()
    name = (form.get("name") or "").strip() or "Unnamed rule"
    condition_logic = "ANY" if (form.get("condition_logic") or "ALL").upper() == "ANY" else "ALL"
    outcome = form.get("outcome") or "PRESENT"
    if outcome not in RULE_OUTCOMES:
        outcome = "PRESENT"
    try:
        priority = int(form.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0
    conditions = _collect_conditions_from_form(form)

    rule = AttendanceRule(
        tenant_id=user.tenant_id, name=name, priority=priority,
        conditions_json=json.dumps(conditions), condition_logic=condition_logic,
        outcome=outcome, is_active=True,
    )
    db.add(rule)
    db.commit()
    return RedirectResponse("/attendance/setup/rules", status_code=303)


@router.post("/setup/rules/{rule_id}/edit")
async def rules_edit(rule_id: str, request: Request,
                      user: User = Depends(require_admin_or_redirect),
                      db: Session = Depends(get_db)):
    rule = db.query(AttendanceRule).filter(
        AttendanceRule.id == rule_id, AttendanceRule.tenant_id == user.tenant_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    form = await request.form()
    rule.name = (form.get("name") or "").strip() or rule.name
    rule.condition_logic = "ANY" if (form.get("condition_logic") or "ALL").upper() == "ANY" else "ALL"
    outcome = form.get("outcome") or rule.outcome
    if outcome in RULE_OUTCOMES:
        rule.outcome = outcome
    try:
        rule.priority = int(form.get("priority") or rule.priority)
    except (TypeError, ValueError):
        pass
    rule.conditions_json = json.dumps(_collect_conditions_from_form(form))
    db.commit()
    return RedirectResponse("/attendance/setup/rules", status_code=303)


@router.post("/setup/rules/{rule_id}/toggle")
def rules_toggle(rule_id: str, user: User = Depends(require_admin_or_redirect),
                  db: Session = Depends(get_db)):
    rule = db.query(AttendanceRule).filter(
        AttendanceRule.id == rule_id, AttendanceRule.tenant_id == user.tenant_id).first()
    if rule:
        rule.is_active = not rule.is_active
        db.commit()
    return RedirectResponse("/attendance/setup/rules", status_code=303)


@router.post("/setup/rules/{rule_id}/delete")
def rules_delete(rule_id: str, user: User = Depends(require_admin_or_redirect),
                  db: Session = Depends(get_db)):
    rule = db.query(AttendanceRule).filter(
        AttendanceRule.id == rule_id, AttendanceRule.tenant_id == user.tenant_id).first()
    if rule:
        db.delete(rule)
        db.commit()
    return RedirectResponse("/attendance/setup/rules", status_code=303)
