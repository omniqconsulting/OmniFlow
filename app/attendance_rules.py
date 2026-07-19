"""
Tenant-configurable attendance day-status rule engine — client feedback #6
("highly custom client specific rules to decide full or half day"). Fixed
condition field catalog + tenant-defined conditions/outcomes, first-match-
wins by ascending priority. Decides PRESENT/HALF_DAY/ABSENT status only —
never computes pay (B4's no-payroll-logic rule still applies here).
"""
import json
from datetime import datetime, time as _time

from sqlalchemy.orm import Session

# ── Condition field catalog ─────────────────────────────────────────────────
# field -> ("time" | "numeric" | "boolean", extractor(record) -> value|None)

def _hours_worked(record):
    if not record or not record.check_in_at or not record.check_out_at:
        return None
    delta = record.check_out_at - record.check_in_at
    return delta.total_seconds() / 3600.0


def _time_of(dt):
    return dt.time() if dt else None


FIELD_CATALOG = {
    "CHECK_IN_TIME":  ("time",    lambda r: _time_of(r.check_in_at) if r else None),
    "CHECK_OUT_TIME": ("time",    lambda r: _time_of(r.check_out_at) if r else None),
    "HOURS_WORKED":   ("numeric", _hours_worked),
    "HAS_CHECK_OUT":  ("boolean", lambda r: bool(r and r.check_out_at)),
    # IN_FENCE: both check-in AND check-out must be in-fence (if a leg hasn't
    # happened yet, that leg is treated as "not violating" — only an explicit
    # False on either leg counts as out-of-fence). Documented choice: "both
    # legs" was picked over "check-in only" so a rule like "half day if punched
    # out off-site" is expressible too.
    "IN_FENCE": ("boolean", lambda r: bool(r) and (r.check_in_in_fence is not False) and (r.check_out_in_fence is not False)),
}

OPERATORS_BY_KIND = {
    "time":    {"BEFORE", "AFTER"},
    "numeric": {"LT", "LTE", "GT", "GTE"},
    "boolean": {"IS_TRUE", "IS_FALSE"},
}


def _parse_time_value(v):
    if isinstance(v, _time):
        return v
    s = str(v).strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _eval_condition(cond, record) -> bool:
    field = cond.get("field")
    operator = cond.get("operator")
    value = cond.get("value")
    kind_extractor = FIELD_CATALOG.get(field)
    if not kind_extractor:
        return False
    kind, extractor = kind_extractor
    if operator not in OPERATORS_BY_KIND.get(kind, set()):
        return False
    actual = extractor(record)
    if actual is None:
        # Missing data (e.g. no check-out yet) => condition doesn't match,
        # rather than erroring.
        return False

    if kind == "time":
        target = _parse_time_value(value)
        if target is None:
            return False
        if operator == "BEFORE":
            return actual < target
        if operator == "AFTER":
            return actual > target
    elif kind == "numeric":
        try:
            target = float(value)
        except (TypeError, ValueError):
            return False
        if operator == "LT":
            return actual < target
        if operator == "LTE":
            return actual <= target
        if operator == "GT":
            return actual > target
        if operator == "GTE":
            return actual >= target
    elif kind == "boolean":
        if operator == "IS_TRUE":
            return actual is True
        if operator == "IS_FALSE":
            return actual is False
    return False


def _rule_matches(rule, record) -> bool:
    try:
        conditions = json.loads(rule.conditions_json or "[]")
    except (TypeError, ValueError):
        return False
    if not conditions:
        return False
    results = [_eval_condition(c, record) for c in conditions]
    if (rule.condition_logic or "ALL").upper() == "ANY":
        return any(results)
    return all(results)


def evaluate_attendance_rules(db: Session, tenant_id: str, record) -> str | None:
    """Loads active rules for the tenant ordered by priority ascending,
    evaluates each rule's conditions against `record` (an AttendanceRecord),
    and returns the first matching rule's outcome (first-match-wins), or
    None if no rule matches — a safe no-op that falls through to the
    mechanical PRESENT/ABSENT fallback in app/attendance.py::_day_status."""
    from .database import AttendanceRule
    rules = db.query(AttendanceRule).filter(
        AttendanceRule.tenant_id == tenant_id,
        AttendanceRule.is_active == True,
    ).order_by(AttendanceRule.priority.asc()).all()
    for rule in rules:
        if _rule_matches(rule, record):
            return rule.outcome
    return None
