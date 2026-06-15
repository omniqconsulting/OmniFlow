"""
AI Context Engine — Phase 5
Snapshots tenant data into a compact, structured context string
that is passed to the LLM as grounding for natural-language queries.

Design principles
─────────────────
• Domain-agnostic: uses label system for all human-facing terms
• No PII in prompts: names only, no phone/email
• Recency-focused: last 30 days unless the query implies otherwise
• Fast: single DB pass per section; no N+1 queries
"""
from __future__ import annotations
from datetime import datetime, timedelta, date
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import (
    Ticket, TicketEvent, User, Branch, Department,
    ChecklistTemplate, ChecklistAssignment,
)
from .analytics import get_org_avg_tat, calc_tat_hours


# ── Helpers ────────────────────────────────────────────────────────────────────

def _pct(part: int, total: int) -> str:
    if not total:
        return "N/A"
    return f"{round(part / total * 100)}%"

def _h(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)} min"
    return f"{round(hours, 1)} hrs"

def _now():
    return datetime.utcnow()

def _since(days: int):
    return _now() - timedelta(days=days)


# ── Section builders ───────────────────────────────────────────────────────────

def _section_org(db: Session, tenant_id: str, L: dict) -> str:
    """Head-count and role breakdown."""
    users = db.query(User).filter(
        User.tenant_id == tenant_id, User.is_deleted == False,
    ).all()
    active = [u for u in users if u.is_active]
    by_role: dict[str, int] = {}
    for u in active:
        by_role[u.role] = by_role.get(u.role, 0) + 1

    branches = db.query(Branch).filter(
        Branch.tenant_id == tenant_id, Branch.is_deleted == False,
    ).count()
    depts = db.query(Department).filter(
        Department.tenant_id == tenant_id, Department.is_deleted == False,
    ).count()

    lines = [
        "## Organisation",
        f"- Active team members: {len(active)} (total accounts: {len(users)})",
        f"- Role breakdown: {', '.join(f'{v} {k}' for k, v in by_role.items())}",
        f"- Branches: {branches}  |  Departments: {depts}",
    ]
    return "\n".join(lines)


def _section_tickets(db: Session, tenant_id: str, L: dict) -> str:
    """Ticket status, priority, overdue, and SLA summary — last 30 days."""
    now = _now()
    since = _since(30)

    all_open = db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id, Ticket.is_deleted == False,
        Ticket.status.notin_(["CLOSED", "DONE"]),
    ).all()

    overdue = [t for t in all_open if t.due_at and t.due_at < now]
    flagged = [t for t in all_open if t.is_flagged]
    critical = [t for t in all_open if t.priority == "CRITICAL"]
    high     = [t for t in all_open if t.priority == "HIGH"]

    closed_30 = db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id, Ticket.is_deleted == False,
        Ticket.status.in_(["CLOSED", "DONE"]),
        Ticket.created_at >= since,
    ).all()

    on_time = sum(
        1 for t in closed_30
        if t.due_at and t.closed_at and t.closed_at <= t.due_at
    )

    org_avg_tat = get_org_avg_tat(db, tenant_id)

    # Status breakdown for open tickets
    status_count: dict[str, int] = {}
    for t in all_open:
        status_count[t.status] = status_count.get(t.status, 0) + 1

    # Team load: assignee → open count
    load: dict[str, int] = {}
    load_names: dict[str, str] = {}
    for t in all_open:
        if t.current_assignee_id:
            load[t.current_assignee_id] = load.get(t.current_assignee_id, 0) + 1
    if load:
        uid_list = list(load.keys())
        members = db.query(User).filter(User.id.in_(uid_list)).all()
        for u in members:
            load_names[u.id] = u.name

    # Top 5 most loaded employees
    sorted_load = sorted(load.items(), key=lambda x: x[1], reverse=True)[:5]
    load_lines = [
        f"  {load_names.get(uid, uid)}: {cnt} open"
        for uid, cnt in sorted_load
    ]

    # Unassigned
    unassigned = sum(1 for t in all_open if not t.current_assignee_id)

    lines = [
        f"## {L.get('Tickets', 'Tickets')} Snapshot (today)",
        f"- Open tickets: {len(all_open)}",
        f"  Status breakdown: {', '.join(f'{v} {k}' for k, v in status_count.items())}",
        f"- Overdue: {len(overdue)} ({_pct(len(overdue), len(all_open))} of open)",
        f"- Flagged: {len(flagged)}  |  CRITICAL priority: {len(critical)}  |  HIGH priority: {len(high)}",
        f"- Unassigned: {unassigned}",
        f"",
        f"## Last 30 Days Performance",
        f"- Tickets closed: {len(closed_30)}",
        f"- On-time closure: {on_time}/{len(closed_30)} ({_pct(on_time, len(closed_30))})",
        f"- Avg. resolution time: {_h(org_avg_tat)}",
        f"",
        f"## Team Load (open tickets per person)",
    ] + load_lines

    return "\n".join(lines)


def _section_employees(db: Session, tenant_id: str, L: dict) -> str:
    """Per-employee KPI summary — last 30 days."""
    from .analytics import get_employee_kpis
    since = _since(30)

    employees = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.is_deleted == False, User.is_active == True,
        User.role.in_(["EMPLOYEE", "MANAGER"]),
    ).all()

    lines = [f"## {L.get('Employees', 'Team Members')} Performance (last 30 days)"]
    for emp in employees:
        kpis = get_employee_kpis(db, emp.id, tenant_id)
        lines.append(
            f"- {emp.name} ({emp.role}): "
            f"closed={kpis['closed_30d']}, "
            f"active={kpis['active_count']}, "
            f"on_time={kpis['on_time_rate']}%, "
            f"checklist_compliance={kpis['compliance_rate']}%, "
            f"avg_tat={_h(kpis['avg_tat_hours'])}, "
            f"avg_ack={_h(kpis['avg_ack_hours'])}"
        )
    if not employees:
        lines.append("  No employees on record yet.")
    return "\n".join(lines)


def _section_checklists(db: Session, tenant_id: str, L: dict) -> str:
    """Checklist compliance overview."""
    now = _now()
    since = _since(30)

    total = db.query(func.count(ChecklistAssignment.id)).filter(
        ChecklistAssignment.tenant_id == tenant_id,
        ChecklistAssignment.due_at >= since,
        ChecklistAssignment.due_at <= now,
    ).scalar() or 0

    done = db.query(func.count(ChecklistAssignment.id)).filter(
        ChecklistAssignment.tenant_id == tenant_id,
        ChecklistAssignment.status == "DONE",
        ChecklistAssignment.due_at >= since,
        ChecklistAssignment.due_at <= now,
    ).scalar() or 0

    overdue = db.query(func.count(ChecklistAssignment.id)).filter(
        ChecklistAssignment.tenant_id == tenant_id,
        ChecklistAssignment.status == "OVERDUE",
    ).scalar() or 0

    templates = db.query(func.count(ChecklistTemplate.id)).filter(
        ChecklistTemplate.tenant_id == tenant_id,
        ChecklistTemplate.is_deleted == False,
    ).scalar() or 0

    lines = [
        f"## {L.get('Checklists', 'Checklists')} (last 30 days)",
        f"- Templates configured: {templates}",
        f"- Assignments in period: {total}",
        f"- Completed: {done} ({_pct(done, total)})",
        f"- Overdue (currently): {overdue}",
    ]
    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────

def build_context(db: Session, tenant_id: str, L: dict) -> str:
    """
    Assemble a compact text snapshot of the tenant's operational data.
    Returned as a string; injected into the system prompt.
    """
    sections = [
        _section_org(db, tenant_id, L),
        _section_tickets(db, tenant_id, L),
        _section_employees(db, tenant_id, L),
        _section_checklists(db, tenant_id, L),
    ]
    header = (
        f"DATA SNAPSHOT — generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"{'─' * 60}\n"
    )
    return header + "\n\n".join(s for s in sections if s)
