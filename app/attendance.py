"""
Attendance & Leave Module — Workstream B, Phases B2-B3.
PWA punch in/out: single check-in/check-out pair per employee per day,
mandatory photo on the first punch of the day, geolocation validated against
the tenant's configured geofence on every punch, and a reason required when
a punch falls outside it. Client-side offline queueing lives in
app/static/js/attendance.js; this module is the server side of that contract.

B3 adds: the team attendance/leave report (Manager scoped to direct reports,
Admin org-wide — same scoping model used everywhere else in the app), the
leave application/approval workflow, and leave-type balances. Nothing here
feeds into or reads from the Performance Appraisal module, per the standing
rule that attendance is explicitly NOT an appraisal input.
"""
import calendar
import math
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .database import get_db, AttendanceRecord, AttendanceGeofence, LeaveRequest, User
from .auth import require_module, get_current_user_or_redirect
from .templates_env import templates

router = APIRouter()


def _redir(url: str):
    return RedirectResponse(url, status_code=303)

_require_attendance = require_module("ATTENDANCE", "ATTENDANCE_MODULE")
_require_attendance_or_redirect = require_module("ATTENDANCE", "ATTENDANCE_MODULE", redirect_unauthenticated=True)

LEAVE_TYPES = ("CASUAL", "SICK", "EARNED", "OTHER")
# Annual allocation per leave type. No existing schema/config for this
# (B1 didn't define balance columns), so these are sensible constants —
# flagged as a judgment call rather than a client-confirmed policy.
LEAVE_ANNUAL_BALANCE = {"CASUAL": 12, "SICK": 12, "EARNED": 15, "OTHER": 0}


def _require_attendance_manager(user: User = Depends(get_current_user_or_redirect), db: Session = Depends(get_db)) -> User:
    """ADMIN/MANAGER + ATTENDANCE_MODULE gate for the team report/approval
    pages — Admin and Manager both always have the ATTENDANCE module per
    get_user_modules(), so only the role + tenant feature flag need checking."""
    from .constants import has_feature
    from .database import Tenant
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Manager or Admin only")
    tenant = db.query(Tenant).get(user.tenant_id)
    if not has_feature(tenant, "ATTENDANCE_MODULE", db):
        raise HTTPException(status_code=403, detail="Attendance module not enabled for this tenant")
    return user


def _team_user_ids(db: Session, user: User) -> list:
    """Manager scope = direct reports + self; Admin scope = every active
    employee in the tenant. Mirrors the manager_id scoping used throughout
    the rest of the app (e.g. app/main.py's hot-tasks query)."""
    if user.role == "ADMIN":
        return [u.id for u in db.query(User).filter(
            User.tenant_id == user.tenant_id, User.is_deleted == False,
        ).all()]
    team_ids = [u.id for u in db.query(User).filter(
        User.manager_id == user.id, User.is_deleted == False,
    ).all()]
    team_ids.append(user.id)
    return team_ids


def _haversine_meters(lat1, lng1, lat2, lng2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _check_geofence(db: Session, tenant_id: str, branch_id, lat: float, lng: float):
    """Returns (in_fence: bool | None, distance_m: float | None).
    None means no geofence is configured (per branch or tenant-wide default)
    — punches are accepted unvalidated in that case."""
    geo = None
    if branch_id:
        geo = db.query(AttendanceGeofence).filter(
            AttendanceGeofence.tenant_id == tenant_id,
            AttendanceGeofence.branch_id == branch_id,
            AttendanceGeofence.is_deleted == False,
        ).first()
    if geo is None:
        geo = db.query(AttendanceGeofence).filter(
            AttendanceGeofence.tenant_id == tenant_id,
            AttendanceGeofence.branch_id == None,
            AttendanceGeofence.is_deleted == False,
        ).first()
    if geo is None:
        return None, None
    distance = _haversine_meters(lat, lng, geo.center_lat, geo.center_lng)
    return distance <= (geo.radius_meters or 200), distance


@router.get("/attendance", response_class=HTMLResponse)
def attendance_page(
    request: Request,
    user: User = Depends(_require_attendance_or_redirect),
    db: Session = Depends(get_db),
):
    from .setup_routes import _nav_ctx, _L, _unread

    today = date.today()
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.tenant_id == user.tenant_id,
        AttendanceRecord.user_id == user.id,
        AttendanceRecord.record_date == today,
        AttendanceRecord.is_deleted == False,
    ).first()

    geo = None
    if user.branch_id:
        geo = db.query(AttendanceGeofence).filter(
            AttendanceGeofence.tenant_id == user.tenant_id,
            AttendanceGeofence.branch_id == user.branch_id,
            AttendanceGeofence.is_deleted == False,
        ).first()
    if geo is None:
        geo = db.query(AttendanceGeofence).filter(
            AttendanceGeofence.tenant_id == user.tenant_id,
            AttendanceGeofence.branch_id == None,
            AttendanceGeofence.is_deleted == False,
        ).first()

    ctx = {
        "request": request, "user": user, "L": _L(db, user), "unread": _unread(db, user),
        "record": record, "geofence": geo,
    }
    ctx.update(_nav_ctx(db, user))
    return templates.TemplateResponse("attendance_punch.html", ctx)


@router.post("/attendance/punch")
async def attendance_punch(
    lat: float = Form(...),
    lng: float = Form(...),
    out_of_fence_reason: str = Form(""),
    photo: UploadFile = File(None),
    user: User = Depends(_require_attendance),
    db: Session = Depends(get_db),
):
    """Single endpoint for both check-in and check-out — direction is
    inferred from today's existing record, so the client (including the
    offline-queue replay path) doesn't need to know which one it's sending.
    Idempotent per (user, day, direction): a duplicate check-in/out replay
    (e.g. the offline queue flushing twice) is a no-op, not a second row."""
    today = date.today()
    now = datetime.utcnow()

    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.tenant_id == user.tenant_id,
        AttendanceRecord.user_id == user.id,
        AttendanceRecord.record_date == today,
        AttendanceRecord.is_deleted == False,
    ).first()

    in_fence, distance_m = _check_geofence(db, user.tenant_id, user.branch_id, lat, lng)
    if in_fence is False and not out_of_fence_reason.strip():
        raise HTTPException(400, "You're outside the configured site radius — a reason is required to punch in/out.")

    if record is None:
        # First punch of the day — check-in. Photo is mandatory.
        if not photo or not photo.filename:
            raise HTTPException(400, "A photo is required for your first punch of the day.")
        from .uploads import save_upload
        info = await save_upload(photo, user.tenant_id)
        record = AttendanceRecord(
            tenant_id=user.tenant_id, user_id=user.id, branch_id=user.branch_id,
            record_date=today, check_in_at=now, check_in_lat=lat, check_in_lng=lng,
            check_in_in_fence=in_fence, check_in_photo_path=info["file_path"],
            out_of_fence_reason=out_of_fence_reason.strip() or None,
        )
        db.add(record)
        db.commit()
        return JSONResponse({"ok": True, "punch": "IN", "in_fence": in_fence})

    if record.check_out_at is None:
        record.check_out_at = now
        record.check_out_lat = lat
        record.check_out_lng = lng
        if in_fence is False:
            reason = out_of_fence_reason.strip()
            record.out_of_fence_reason = (
                f"{record.out_of_fence_reason} | Checkout: {reason}" if record.out_of_fence_reason else f"Checkout: {reason}"
            )
        db.commit()
        return JSONResponse({"ok": True, "punch": "OUT", "in_fence": in_fence})

    # Already punched in and out today — idempotent no-op for a replayed
    # offline-queue item rather than an error, so a duplicate sync never
    # creates a second record or surfaces as a failure to the employee.
    return JSONResponse({"ok": True, "punch": "ALREADY_DONE"})


# ══════════════════════════════════════════════════════════════════════════════
# B3 — Team attendance/leave report (Manager: own team, Admin: org-wide)
# ══════════════════════════════════════════════════════════════════════════════

_OUTCOME_TO_CODE = {"PRESENT": "P", "HALF_DAY": "H", "ABSENT": "A"}


def _day_status(db: Session, tenant_id: str, records_by_date: dict, leaves: list, day: date) -> str:
    """Returns a short code for the day-status grid — P (present), H
    (half-day), L (on leave — an approved leave covers this day), FUTURE
    (day hasn't happened yet), or A (absent). Leave always wins over absent
    — the exact acceptance criterion from B1: an approved leave date must
    show as "on leave", never "absent".

    For a day with a punch record: the manual is_half_day toggle (B4) is
    treated as an explicit override and short-circuits rule evaluation;
    otherwise the tenant's own attendance rules (B5) are evaluated in
    priority order and the first match's outcome wins; if no rule matches
    (including tenants with zero rules configured — no behavior change from
    before B5), it falls back to plain Present."""
    rec = records_by_date.get(day)
    if rec and rec.check_in_at:
        if rec.is_half_day:
            return "H"
        from .attendance_rules import evaluate_attendance_rules
        outcome = evaluate_attendance_rules(db, tenant_id, rec)
        return _OUTCOME_TO_CODE.get(outcome, "P")
    for lv in leaves:
        if lv.date_from <= day <= lv.date_to:
            return "H" if lv.is_half_day else "L"
    if day > date.today():
        return "FUTURE"
    return "A"


@router.get("/attendance/team", response_class=HTMLResponse)
def attendance_team_report(
    request: Request,
    year: int = None,
    month: int = None,
    user: User = Depends(_require_attendance_manager),
    db: Session = Depends(get_db),
):
    """Attendance log + per-employee out-of-fence count + day-by-day
    Present/On Leave/Absent grid for the selected month, scoped to the
    manager's own team or org-wide for admin."""
    from .setup_routes import _nav_ctx, _L, _unread

    today = date.today()
    year = year or today.year
    month = month or today.month
    days_in_month = calendar.monthrange(year, month)[1]
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    team_ids = _team_user_ids(db, user)
    employees = db.query(User).filter(User.id.in_(team_ids)).order_by(User.name).all()

    records = db.query(AttendanceRecord).filter(
        AttendanceRecord.tenant_id == user.tenant_id,
        AttendanceRecord.user_id.in_(team_ids),
        AttendanceRecord.record_date >= month_start,
        AttendanceRecord.record_date <= month_end,
        AttendanceRecord.is_deleted == False,
    ).order_by(AttendanceRecord.record_date.desc()).all()

    leaves = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == user.tenant_id,
        LeaveRequest.user_id.in_(team_ids),
        LeaveRequest.status == "APPROVED",
        LeaveRequest.date_from <= month_end,
        LeaveRequest.date_to >= month_start,
        LeaveRequest.is_deleted == False,
    ).all()

    records_by_emp = {}
    for r in records:
        records_by_emp.setdefault(r.user_id, {})[r.record_date] = r
    leaves_by_emp = {}
    for lv in leaves:
        leaves_by_emp.setdefault(lv.user_id, []).append(lv)

    day_list = [date(year, month, d) for d in range(1, days_in_month + 1)]
    grid = []
    for emp in employees:
        emp_records = records_by_emp.get(emp.id, {})
        emp_leaves = leaves_by_emp.get(emp.id, [])
        out_of_fence_count = sum(
            1 for r in emp_records.values()
            if r.check_in_in_fence is False or (r.check_out_at and "Checkout:" in (r.out_of_fence_reason or ""))
        )
        row = {
            "employee": emp,
            "days": [_day_status(db, user.tenant_id, emp_records, emp_leaves, d) for d in day_list],
            "record_ids": [emp_records[d].id if d in emp_records else None for d in day_list],
            "out_of_fence_count": out_of_fence_count,
        }
        grid.append(row)

    pending_leaves = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == user.tenant_id,
        LeaveRequest.user_id.in_(team_ids),
        LeaveRequest.status == "PENDING",
        LeaveRequest.is_deleted == False,
    ).order_by(LeaveRequest.created_at.desc()).all()

    ctx = {
        "request": request, "user": user, "L": _L(db, user), "unread": _unread(db, user),
        "grid": grid, "day_list": day_list, "records": records,
        "pending_leaves": pending_leaves, "year": year, "month": month,
        "month_name": calendar.month_name[month],
    }
    ctx.update(_nav_ctx(db, user))
    return templates.TemplateResponse("attendance_team.html", ctx)


@router.post("/attendance/team/records/{record_id}/toggle-half-day")
def toggle_half_day(record_id: str, user: User = Depends(_require_attendance_manager), db: Session = Depends(get_db)):
    """B4 — manual half-day override for a present day (e.g. late arrival /
    early leave). Structure/reconciliation only: this just flips a flag for
    a future payroll consumer to read, it computes no pay or hours itself."""
    rec = db.query(AttendanceRecord).filter(
        AttendanceRecord.id == record_id, AttendanceRecord.tenant_id == user.tenant_id,
    ).first()
    if not rec:
        raise HTTPException(404, "Attendance record not found")
    if user.role != "ADMIN" and rec.user_id not in _team_user_ids(db, user):
        raise HTTPException(403, "Not your team member")
    rec.is_half_day = not rec.is_half_day
    db.commit()
    return _redir(f"/attendance/team?year={rec.record_date.year}&month={rec.record_date.month}")


@router.post("/attendance/leave/{leave_id}/approve")
def leave_approve(leave_id: str, user: User = Depends(_require_attendance_manager), db: Session = Depends(get_db)):
    lv = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id, LeaveRequest.tenant_id == user.tenant_id).first()
    if not lv:
        raise HTTPException(404, "Leave request not found")
    if user.role != "ADMIN" and lv.user_id not in _team_user_ids(db, user):
        raise HTTPException(403, "Not your team member")
    lv.status = "APPROVED"
    lv.approver_id = user.id
    lv.decided_at = datetime.utcnow()
    db.commit()
    return _redir("/attendance/team")


@router.post("/attendance/leave/{leave_id}/reject")
def leave_reject(leave_id: str, user: User = Depends(_require_attendance_manager), db: Session = Depends(get_db)):
    lv = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id, LeaveRequest.tenant_id == user.tenant_id).first()
    if not lv:
        raise HTTPException(404, "Leave request not found")
    if user.role != "ADMIN" and lv.user_id not in _team_user_ids(db, user):
        raise HTTPException(403, "Not your team member")
    lv.status = "REJECTED"
    lv.approver_id = user.id
    lv.decided_at = datetime.utcnow()
    db.commit()
    return _redir("/attendance/team")


# ══════════════════════════════════════════════════════════════════════════════
# Employee-facing leave application + own history/balance
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/attendance/leave", response_class=HTMLResponse)
def leave_page(
    request: Request,
    user: User = Depends(_require_attendance_or_redirect),
    db: Session = Depends(get_db),
):
    from .setup_routes import _nav_ctx, _L, _unread

    year_start = date(date.today().year, 1, 1)
    my_leaves = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == user.tenant_id,
        LeaveRequest.user_id == user.id,
        LeaveRequest.is_deleted == False,
    ).order_by(LeaveRequest.created_at.desc()).all()

    used_by_type = {t: 0 for t in LEAVE_TYPES}
    for lv in my_leaves:
        if lv.status == "APPROVED" and lv.date_from >= year_start:
            used_by_type[lv.leave_type] = used_by_type.get(lv.leave_type, 0) + (lv.date_to - lv.date_from).days + 1

    balances = [
        {"type": t, "allocated": LEAVE_ANNUAL_BALANCE[t], "used": used_by_type[t],
         "remaining": max(0, LEAVE_ANNUAL_BALANCE[t] - used_by_type[t])}
        for t in LEAVE_TYPES
    ]

    ctx = {
        "request": request, "user": user, "L": _L(db, user), "unread": _unread(db, user),
        "my_leaves": my_leaves, "balances": balances, "leave_types": LEAVE_TYPES,
    }
    ctx.update(_nav_ctx(db, user))
    return templates.TemplateResponse("attendance_leave.html", ctx)


@router.post("/attendance/leave/apply")
def leave_apply(
    leave_type: str = Form(...),
    date_from: str = Form(...),
    date_to: str = Form(...),
    reason: str = Form(""),
    is_half_day: str = Form(""),
    user: User = Depends(_require_attendance),
    db: Session = Depends(get_db),
):
    if leave_type not in LEAVE_TYPES:
        raise HTTPException(400, "Invalid leave type")
    try:
        d_from = datetime.fromisoformat(date_from).date()
        d_to = datetime.fromisoformat(date_to).date()
    except ValueError:
        raise HTTPException(400, "Invalid date range")
    if d_to < d_from:
        raise HTTPException(400, "End date must be on or after start date")
    half_day = is_half_day == "true"
    if half_day and d_from != d_to:
        raise HTTPException(400, "Half-day leave must be a single day")

    db.add(LeaveRequest(
        tenant_id=user.tenant_id, user_id=user.id, leave_type=leave_type,
        date_from=d_from, date_to=d_to, reason=reason.strip() or None,
        is_half_day=half_day,
    ))
    db.commit()
    return _redir("/attendance/leave")
