from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..attendance import _check_in_fence, _haversine_m, _resolve_geofence, get_self_month_calendar
from ..database import (
    AttendanceGeofence, AttendanceRecord, Branch, LeaveRequest, Tenant, User, get_db,
)
from ..uploads import save_upload
from .features import require_feature
from .pagination import paginate_cursor
from .schemas import Page
from .security import get_current_api_user, limiter

router = APIRouter(prefix="/attendance", tags=["Attendance"], dependencies=[Depends(require_feature("ATTENDANCE"))])


def _branch_names(db: Session, tenant_id: str) -> dict:
    return {b.id: b.name for b in db.query(Branch).filter(Branch.tenant_id == tenant_id).all()}


class AttendanceRecordOut(BaseModel):
    id: str
    user_id: str
    work_date: date
    branch_id: Optional[str]
    branch_name: Optional[str]
    check_in_at: Optional[datetime]
    check_in_lat: Optional[float]
    check_in_lng: Optional[float]
    check_in_in_fence: Optional[bool]
    check_in_reason: Optional[str]
    check_out_at: Optional[datetime]
    check_out_lat: Optional[float]
    check_out_lng: Optional[float]
    check_out_in_fence: Optional[bool]
    check_out_reason: Optional[str]
    photo_path: Optional[str]
    is_half_day: bool
    recorded_by_name: Optional[str]
    on_behalf_reason: Optional[str]


class LeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    leave_type: str
    start_date: date
    end_date: date
    is_half_day: bool
    status: str
    created_at: datetime


def _record_out(record: AttendanceRecord, branch_names: dict, user_names: dict) -> AttendanceRecordOut:
    return AttendanceRecordOut(
        id=record.id, user_id=record.user_id, work_date=record.work_date,
        branch_id=record.branch_id, branch_name=branch_names.get(record.branch_id),
        check_in_at=record.check_in_at, check_in_lat=record.check_in_lat, check_in_lng=record.check_in_lng,
        check_in_in_fence=record.check_in_in_fence, check_in_reason=record.check_in_reason,
        check_out_at=record.check_out_at, check_out_lat=record.check_out_lat, check_out_lng=record.check_out_lng,
        check_out_in_fence=record.check_out_in_fence, check_out_reason=record.check_out_reason,
        photo_path=record.photo_path, is_half_day=bool(record.is_half_day),
        recorded_by_name=user_names.get(record.recorded_by_id), on_behalf_reason=record.on_behalf_reason,
    )


@router.get("/records", response_model=Page[AttendanceRecordOut])
def list_attendance_records(
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(AttendanceRecord).filter(AttendanceRecord.tenant_id == user.tenant_id)
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(AttendanceRecord.user_id == user.id)
    if year and month:
        from calendar import monthrange
        days_in_month = monthrange(year, month)[1]
        q = q.filter(
            AttendanceRecord.work_date >= date(year, month, 1),
            AttendanceRecord.work_date <= date(year, month, days_in_month),
        )
    rows, next_cursor = paginate_cursor(q, AttendanceRecord, cursor, limit, created_col="created_at")
    branch_names = _branch_names(db, user.tenant_id)
    user_names = {u.id: u.name for u in db.query(User).filter(User.tenant_id == user.tenant_id).all()}
    return Page(items=[_record_out(r, branch_names, user_names) for r in rows], next_cursor=next_cursor)


@router.get("/leave", response_model=Page[LeaveRequestOut])
def list_leave_requests(
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(LeaveRequest).filter(LeaveRequest.tenant_id == user.tenant_id)
    if user.role not in ("ADMIN", "MANAGER"):
        q = q.filter(LeaveRequest.user_id == user.id)
    rows, next_cursor = paginate_cursor(q, LeaveRequest, cursor, limit, created_col="created_at")
    return Page(items=rows, next_cursor=next_cursor)


# ── Punch in/out — Phase 0.6/0.7. Mirrors app/attendance.py's punch_page /
# punch_checkin / punch_checkout (same helpers imported above, same geofence
# resolution, same in-fence rule), just returning JSON instead of rendering
# the desktop template. Setup > Access Control's ATTENDANCE gate already
# applies to this whole router (see dependencies= above).
#
# Mobile-only difference from the desktop flow (deliberate, per Sahil):
# the desktop punch page lets the employee pick their branch from a form
# control; the app instead detects which branch's configured geofence the
# employee's coordinates actually fall inside, so there's nothing to pick.
# The desktop route itself is untouched.

class BranchOut(BaseModel):
    id: str
    name: str


class EmployeeOut(BaseModel):
    id: str
    name: str


def _detect_branch_by_geofence(db: Session, tenant_id: str, user: User, lat: float, lng: float):
    """Returns (branch_id_or_None, fence_or_None, in_fence: bool).

    Checks every active branch-specific geofence for this tenant and picks
    the closest one the employee is actually inside. Falls back to the
    tenant-wide default geofence (branch_id IS NULL) if no branch-specific
    one matches, attributing the employee's own default branch in that
    case since a tenant-wide fence can't identify which branch they're at.
    If nothing matches, falls back to the employee's own default branch's
    geofence (possibly none), same as before — out-of-fence still just
    requires a reason rather than blocking the punch outright.
    """
    branch_fences = db.query(AttendanceGeofence).filter(
        AttendanceGeofence.tenant_id == tenant_id,
        AttendanceGeofence.branch_id.isnot(None),
        AttendanceGeofence.is_active == True,
    ).all()

    best = None
    best_dist = None
    for fence in branch_fences:
        dist = _haversine_m(lat, lng, fence.center_lat, fence.center_lng)
        if dist <= fence.radius_m and (best_dist is None or dist < best_dist):
            best, best_dist = fence, dist

    if best:
        return best.branch_id, best, True

    default_fence = db.query(AttendanceGeofence).filter(
        AttendanceGeofence.tenant_id == tenant_id,
        AttendanceGeofence.branch_id.is_(None),
        AttendanceGeofence.is_active == True,
    ).first()
    if default_fence and _haversine_m(lat, lng, default_fence.center_lat, default_fence.center_lng) <= default_fence.radius_m:
        return user.branch_id, default_fence, True

    # No fence matched — fall back to the employee's own branch's geofence
    # (or tenant default), same resolution the desktop uses, and let the
    # existing in-fence/reason rule apply below.
    fallback_fence = _resolve_geofence(db, tenant_id, user.branch_id)
    return user.branch_id, fallback_fence, _check_in_fence(fallback_fence, lat, lng)


def _resolve_on_behalf_target(db: Session, user: User, on_behalf_of_user_id: Optional[str]) -> User:
    """Phase 0.7 — a manager/admin physically with an employee (who has no
    smartphone of their own, say) can capture that employee's photo/GPS and
    submit it on their behalf. Returns the employee the record is actually
    for; raises if the caller isn't allowed to record for that person."""
    if not on_behalf_of_user_id:
        return user
    if user.role not in ("ADMIN", "MANAGER"):
        raise HTTPException(status_code=403, detail="Only an admin or manager can record attendance on someone else's behalf.")
    target = db.query(User).filter(
        User.id == on_behalf_of_user_id, User.tenant_id == user.tenant_id, User.is_deleted == False,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found")
    if user.role == "MANAGER" and target.manager_id != user.id and target.id != user.id:
        raise HTTPException(status_code=403, detail="You can only record attendance for your direct reports.")
    return target


@router.get("/on-behalf-targets", response_model=list[EmployeeOut])
def list_on_behalf_targets(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    """Employees the caller is allowed to record attendance for on their
    behalf — ADMIN sees the whole tenant, MANAGER sees only their direct
    reports, EMPLOYEE sees none (empty list, not an error, so the app can
    just hide the option)."""
    if user.role == "ADMIN":
        q = db.query(User).filter(User.tenant_id == user.tenant_id, User.is_deleted == False, User.id != user.id)
    elif user.role == "MANAGER":
        q = db.query(User).filter(User.tenant_id == user.tenant_id, User.is_deleted == False, User.manager_id == user.id)
    else:
        return []
    # Some tenants have genuine duplicate employee rows sharing the same
    # phone number (seen in real data — repeated bulk-import test runs
    # left several copies of the same person with sequential employee_id
    # values, not just a coincidence of shared names). Deduping by id alone
    # doesn't catch that since each copy is a distinct row. Group by phone
    # (the real identity signal for a duplicated real person) and keep the
    # earliest-created row as the one this picker offers — the other
    # rows aren't touched/deleted here, just not shown as separate options.
    rows = q.order_by(User.created_at.asc()).all()
    seen_phones = set()
    seen_ids = set()
    result = []
    for u in rows:
        key = (u.phone or "").strip() or None
        if key is not None:
            if key in seen_phones:
                continue
            seen_phones.add(key)
        elif u.id in seen_ids:
            continue
        seen_ids.add(u.id)
        result.append(EmployeeOut(id=u.id, name=u.name))
    return sorted(result, key=lambda e: e.name)


class TodayRecordOut(BaseModel):
    check_in_at: Optional[datetime]
    check_out_at: Optional[datetime]
    check_in_in_fence: Optional[bool]
    check_out_in_fence: Optional[bool]
    checkin_distance_m: Optional[int]
    checkout_distance_m: Optional[int]
    branch_name: Optional[str]


class PunchStatusOut(BaseModel):
    has_fence: bool
    fence_lat: Optional[float]
    fence_lng: Optional[float]
    fence_radius_m: Optional[int]
    record: Optional[TodayRecordOut]


@router.get("/punch-status", response_model=PunchStatusOut)
def punch_status(
    on_behalf_of_user_id: Optional[str] = Query(None),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    target = _resolve_on_behalf_target(db, user, on_behalf_of_user_id)
    today = date.today()
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.user_id == target.id, AttendanceRecord.work_date == today,
    ).first()

    effective_branch_id = (record.branch_id if record and record.branch_id else None) or target.branch_id
    fence = _resolve_geofence(db, target.tenant_id, effective_branch_id)

    record_out = None
    if record:
        branch = db.query(Branch).filter(Branch.id == record.branch_id).first() if record.branch_id else None
        checkin_distance_m = None
        if fence and record.check_in_lat is not None:
            checkin_distance_m = round(_haversine_m(record.check_in_lat, record.check_in_lng, fence.center_lat, fence.center_lng))
        checkout_distance_m = None
        if fence and record.check_out_lat is not None:
            checkout_distance_m = round(_haversine_m(record.check_out_lat, record.check_out_lng, fence.center_lat, fence.center_lng))
        record_out = TodayRecordOut(
            check_in_at=record.check_in_at, check_out_at=record.check_out_at,
            check_in_in_fence=record.check_in_in_fence, check_out_in_fence=record.check_out_in_fence,
            checkin_distance_m=checkin_distance_m, checkout_distance_m=checkout_distance_m,
            branch_name=branch.name if branch else None,
        )

    return PunchStatusOut(
        has_fence=bool(fence),
        fence_lat=fence.center_lat if fence else None,
        fence_lng=fence.center_lng if fence else None,
        fence_radius_m=fence.radius_m if fence else None,
        record=record_out,
    )


class CheckInOut(BaseModel):
    ok: bool
    check_in_at: datetime
    in_fence: bool
    branch_name: Optional[str]
    recorded_for_name: Optional[str]


class CheckOutOut(BaseModel):
    ok: bool
    check_out_at: datetime
    in_fence: bool
    recorded_for_name: Optional[str]


@router.post("/punch/checkin", response_model=CheckInOut)
@limiter.limit("10/minute")
async def punch_checkin(
    request: Request,
    lat: float = Form(...), lng: float = Form(...),
    reason: Optional[str] = Form(None),
    photo: UploadFile = File(...),
    on_behalf_of_user_id: Optional[str] = Form(None),
    on_behalf_reason: Optional[str] = Form(None),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    target = _resolve_on_behalf_target(db, user, on_behalf_of_user_id)
    is_on_behalf = target.id != user.id
    if is_on_behalf and not (on_behalf_reason or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required when recording attendance on someone else's behalf.")

    today = date.today()
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.user_id == target.id, AttendanceRecord.work_date == today,
    ).first()
    if record and record.check_in_at:
        raise HTTPException(status_code=400, detail="Already checked in today")

    effective_branch_id, fence, in_fence = _detect_branch_by_geofence(db, target.tenant_id, target, lat, lng)
    reason = (reason or "").strip() or None
    if not in_fence and not reason:
        raise HTTPException(status_code=400, detail="You're outside the office zone — please add a reason to check in.")

    upload = await save_upload(photo, target.tenant_id, allowed_kinds=("image",))

    if not record:
        record = AttendanceRecord(tenant_id=target.tenant_id, user_id=target.id, work_date=today)
        db.add(record)

    record.branch_id = effective_branch_id
    record.check_in_at = datetime.utcnow()
    record.check_in_lat = lat
    record.check_in_lng = lng
    record.check_in_in_fence = in_fence
    record.check_in_reason = reason
    record.photo_path = upload.get("file_path")
    if is_on_behalf:
        record.recorded_by_id = user.id
        record.on_behalf_reason = on_behalf_reason.strip()
    db.commit()

    branch = db.query(Branch).filter(Branch.id == effective_branch_id).first() if effective_branch_id else None
    return CheckInOut(
        ok=True, check_in_at=record.check_in_at, in_fence=in_fence,
        branch_name=branch.name if branch else None,
        recorded_for_name=target.name if is_on_behalf else None,
    )


@router.post("/punch/checkout", response_model=CheckOutOut)
@limiter.limit("10/minute")
def punch_checkout(
    request: Request,
    lat: float = Form(...), lng: float = Form(...),
    reason: Optional[str] = Form(None),
    on_behalf_of_user_id: Optional[str] = Form(None),
    on_behalf_reason: Optional[str] = Form(None),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    target = _resolve_on_behalf_target(db, user, on_behalf_of_user_id)
    is_on_behalf = target.id != user.id
    if is_on_behalf and not (on_behalf_reason or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required when recording attendance on someone else's behalf.")

    today = date.today()
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.user_id == target.id, AttendanceRecord.work_date == today,
    ).first()
    if not record or not record.check_in_at:
        raise HTTPException(status_code=400, detail="Check in first")
    if record.check_out_at:
        raise HTTPException(status_code=400, detail="Already checked out today")

    fence = _resolve_geofence(db, target.tenant_id, record.branch_id or target.branch_id)
    in_fence = _check_in_fence(fence, lat, lng)
    reason = (reason or "").strip() or None
    if not in_fence and not reason:
        raise HTTPException(status_code=400, detail="You're outside the office zone — please add a reason to check out.")

    record.check_out_at = datetime.utcnow()
    record.check_out_lat = lat
    record.check_out_lng = lng
    record.check_out_in_fence = in_fence
    record.check_out_reason = reason
    if is_on_behalf:
        record.recorded_by_id = user.id
        # Preserve the check-in's on_behalf_reason if it already had one;
        # a checkout-time reason further explains the checkout specifically.
        record.on_behalf_reason = on_behalf_reason.strip()
    db.commit()

    return CheckOutOut(ok=True, check_out_at=record.check_out_at, in_fence=in_fence, recorded_for_name=target.name if is_on_behalf else None)


class LeaveApplyRequest(BaseModel):
    leave_type: str = "CASUAL"
    start_date: date
    end_date: date
    is_half_day: bool = False
    reason: Optional[str] = None


@router.post("/leave/apply", response_model=LeaveRequestOut)
def leave_apply(body: LeaveApplyRequest, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="End date must be on or after start date")

    # Same table/status flow app/attendance.py's leave_apply writes to
    # (LeaveRequest, status="PENDING") — a manager/admin approving it on the
    # website's Organization > Attendance & Leave page is the same row, no
    # separate mobile leave queue.
    req = LeaveRequest(
        tenant_id=user.tenant_id, user_id=user.id, leave_type=body.leave_type,
        start_date=body.start_date, end_date=body.end_date,
        is_half_day=bool(body.is_half_day) and body.start_date == body.end_date,
        reason=(body.reason or "").strip() or None, status="PENDING",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


# ── Setup > Access Control — geofence config (read-only for now; write/edit
# stays desktop-only at /attendance/setup/geofence until mobile Setup
# management is actually asked for). Admin-only, mirrors geofence_setup's
# GET data exactly.

class GeofenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    branch_id: Optional[str]
    site_name: str
    center_lat: float
    center_lng: float
    radius_m: int
    is_active: bool


@router.get("/geofences", response_model=list[GeofenceOut])
def list_geofences(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin only")
    return db.query(AttendanceGeofence).filter(
        AttendanceGeofence.tenant_id == user.tenant_id,
    ).order_by(AttendanceGeofence.created_at.desc()).all()


# ── Month calendar — reuses get_self_month_calendar (app/attendance.py),
# the exact same function the desktop My Tasks Attendance tab calls. Its
# day-status classification runs through _day_status -> evaluate_attendance_
# rules, which reads the tenant's real AttendanceRule rows configured at
# Setup > Attendance Rules — so a rule change there is reflected here too,
# not a separate/simplified status calculation reimplemented for mobile.

class CalendarDayOut(BaseModel):
    date: date
    status: str


class MonthCalendarOut(BaseModel):
    year: int
    month: int
    month_name: str
    days: list[CalendarDayOut]


@router.get("/calendar", response_model=MonthCalendarOut)
def month_calendar(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    result = get_self_month_calendar(db, user, year, month)
    return MonthCalendarOut(
        year=result["year"], month=result["month"], month_name=result["month_name"],
        days=[CalendarDayOut(date=d["date"], status=d["status"]) for d in result["days"]],
    )
