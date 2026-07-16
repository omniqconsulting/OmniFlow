"""
Attendance & Leave Module — Workstream B, Phase B5.
Tenant-defined attendance classification rules: each tenant can define their
own rules (late arrival, minimum hours, missed punch-out, etc.) from a fixed
catalog of condition fields/operators — there is no fixed catalog of rule
*types*, per client decision 2026-07-16. Rules are evaluated in ascending
`priority` order; the first matching rule's outcome wins.
"""
import json
from datetime import datetime, time

# ── Condition field catalog ──────────────────────────────────────────────────
# Each field maps to (kind, extractor). kind decides which operators are valid
# and how the stored value is parsed/compared.
FIELD_CHOICES = {
    "CHECK_IN_TIME":   {"label": "Check-in time",        "kind": "time"},
    "CHECK_OUT_TIME":  {"label": "Check-out time",        "kind": "time"},
    "HOURS_WORKED":    {"label": "Hours worked",          "kind": "number"},
    "HAS_CHECK_OUT":   {"label": "Has checked out",       "kind": "bool"},
    "IN_FENCE":        {"label": "Check-in within geofence", "kind": "bool"},
}

OPERATORS_BY_KIND = {
    "time":   [("BEFORE", "is before"), ("AFTER", "is after")],
    "number": [("LT", "is less than"), ("LTE", "is at most"), ("GT", "is greater than"), ("GTE", "is at least")],
    "bool":   [("IS_TRUE", "is true"), ("IS_FALSE", "is false")],
}

OUTCOME_CHOICES = ("PRESENT", "HALF_DAY", "ABSENT")


def _extract(field: str, record):
    kind = FIELD_CHOICES[field]["kind"]
    if field == "CHECK_IN_TIME":
        return record.check_in_at.time() if record.check_in_at else None
    if field == "CHECK_OUT_TIME":
        return record.check_out_at.time() if record.check_out_at else None
    if field == "HOURS_WORKED":
        if record.check_in_at and record.check_out_at:
            return (record.check_out_at - record.check_in_at).total_seconds() / 3600.0
        return None
    if field == "HAS_CHECK_OUT":
        return record.check_out_at is not None
    if field == "IN_FENCE":
        return bool(record.check_in_in_fence)
    return None


def _parse_value(kind: str, raw):
    if raw is None or raw == "":
        return None
    if kind == "time":
        return datetime.strptime(raw, "%H:%M").time() if isinstance(raw, str) else raw
    if kind == "number":
        return float(raw)
    if kind == "bool":
        return None  # bool operators carry no comparison value
    return raw


def _condition_matches(cond: dict, record) -> bool:
    field = cond.get("field")
    op = cond.get("operator")
    if field not in FIELD_CHOICES:
        return False
    kind = FIELD_CHOICES[field]["kind"]
    actual = _extract(field, record)
    if actual is None and kind != "bool":
        return False  # e.g. no check-out yet — a time/hours condition can't match

    if kind == "bool":
        return actual is True if op == "IS_TRUE" else actual is False
    if kind == "time":
        target = _parse_value("time", cond.get("value"))
        if target is None:
            return False
        return actual < target if op == "BEFORE" else actual > target
    if kind == "number":
        target = _parse_value("number", cond.get("value"))
        if target is None:
            return False
        if op == "LT":  return actual < target
        if op == "LTE": return actual <= target
        if op == "GT":  return actual > target
        if op == "GTE": return actual >= target
    return False


def _rule_matches(rule, record) -> bool:
    try:
        conditions = json.loads(rule.conditions_json or "[]")
    except (ValueError, TypeError):
        return False
    if not conditions:
        return False
    results = [_condition_matches(c, record) for c in conditions]
    return all(results) if (rule.condition_logic or "ALL") == "ALL" else any(results)


def evaluate_attendance_rules(db, tenant_id: str, record) -> "str | None":
    """Returns the outcome ('PRESENT' / 'HALF_DAY' / 'ABSENT') of the first
    active rule (ascending priority) that matches this record, or None if no
    rule matches — caller falls back to the existing plain-presence logic in
    that case, so a tenant with zero rules configured sees no behavior
    change from before B5."""
    from .database import AttendanceRule
    rules = db.query(AttendanceRule).filter(
        AttendanceRule.tenant_id == tenant_id,
        AttendanceRule.is_active == True,
        AttendanceRule.is_deleted == False,
    ).order_by(AttendanceRule.priority.asc()).all()
    for rule in rules:
        if _rule_matches(rule, record):
            return rule.outcome
    return None
