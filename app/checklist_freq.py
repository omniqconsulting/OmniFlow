"""
Shared checklist due-date computation — custom frequency rules (E-14 extended
types + nth-weekday rules) and the due-date-vs-due-time distinction.

Used by both app/scheduler.py (daily generation job) and app/main.py
(template edit → retroactive recompute of pending assignments), so the two
never drift out of sync with each other.
"""
import calendar
from datetime import datetime, timedelta

CUSTOM_FREQUENCY_TYPES = (
    "WEEKLY_CUSTOM", "MONTHLY_DATE", "YEARLY_DATE",
    "NTH_WEEKDAY_MONTH", "NTH_WEEKDAY_QUARTER",
)


def nth_weekday_of_month(year: int, month: int, nth: int, weekday: int):
    """weekday: 0=Monday..6=Sunday. nth: 1-4 for 1st-4th occurrence, -1 for last.
    Returns a date, or None if that occurrence doesn't exist in this month
    (e.g. asking for a 5th Friday)."""
    if nth == -1:
        last_day = calendar.monthrange(year, month)[1]
        d = datetime(year, month, last_day).date()
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d
    first = datetime(year, month, 1).date()
    offset = (weekday - first.weekday()) % 7
    d = first + timedelta(days=offset + 7 * (nth - 1))
    if d.month != month:
        return None
    return d


def quarter_start_month(month: int) -> int:
    return ((month - 1) // 3) * 3 + 1


def apply_due_time(date_obj, tmpl) -> datetime:
    """Combine a due date with the template's configured due time.
    ANYTIME (default) keeps the historical 18:00 cutoff used across the app;
    FIXED_TIME uses the admin-configured HH:MM."""
    hh, mm = 18, 0
    if (getattr(tmpl, "due_time_mode", None) or "ANYTIME") == "FIXED_TIME":
        raw = getattr(tmpl, "due_time", None)
        if raw:
            try:
                parts = raw.split(":")
                hh, mm = int(parts[0]), int(parts[1])
            except Exception:
                hh, mm = 18, 0
    return datetime(date_obj.year, date_obj.month, date_obj.day, hh, mm, 0)


def _date_matches_custom_rule(check_date, ft: str, cfg: dict) -> bool:
    cfg = cfg or {}
    if ft == "WEEKLY_CUSTOM":
        return check_date.weekday() in cfg.get("days", [])
    if ft == "MONTHLY_DATE":
        return check_date.day == cfg.get("day", 1)
    if ft == "YEARLY_DATE":
        return check_date.month == cfg.get("month", 1) and check_date.day == cfg.get("day", 1)
    if ft == "NTH_WEEKDAY_MONTH":
        d = nth_weekday_of_month(check_date.year, check_date.month, cfg.get("nth", 1), cfg.get("weekday", 0))
        return d == check_date
    if ft == "NTH_WEEKDAY_QUARTER":
        qsm = quarter_start_month(check_date.month)
        d = nth_weekday_of_month(check_date.year, qsm, cfg.get("nth", 1), cfg.get("weekday", 0))
        return d == check_date
    return False


def matches_today(tmpl, now: datetime) -> bool:
    """True if `now`'s date satisfies the template's custom frequency rule."""
    ft = getattr(tmpl, "frequency_type", None)
    if ft not in CUSTOM_FREQUENCY_TYPES:
        return False
    return _date_matches_custom_rule(now.date(), ft, getattr(tmpl, "frequency_config", None))


def next_custom_occurrence(tmpl, from_dt: datetime, max_days: int = 400):
    """Next due datetime (> from_dt) matching the template's custom frequency
    rule, honoring its due-time setting. Scans forward day by day — rules are
    all calendar-based (nth weekday, fixed day-of-month/year), so a bounded
    linear scan is simple and always terminates well within max_days."""
    ft = getattr(tmpl, "frequency_type", None)
    if ft not in CUSTOM_FREQUENCY_TYPES:
        return None
    cfg = getattr(tmpl, "frequency_config", None)
    start = from_dt.date()
    for i in range(max_days):
        check_date = start + timedelta(days=i)
        if _date_matches_custom_rule(check_date, ft, cfg):
            due = apply_due_time(check_date, tmpl)
            if due > from_dt:
                return due
    return None
