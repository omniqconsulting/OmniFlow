"""
AI Context Engine — Phase 5
Snapshots tenant data into a compact, structured context string
that is passed to the LLM as grounding for natural-language queries.

Design principles
─────────────────
• Domain-agnostic: uses label system for all human-facing terms
• No PII in prompts: names only, no phone/email
• Recency-focused: last 30 days unless the query implies otherwise
• Fast: bulk queries per section; no N+1 queries
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
    """Organisation structure: branches, departments, headcount per dept."""
    users = db.query(User).filter(
        User.tenant_id == tenant_id, User.is_deleted == False,
    ).all()
    active = [u for u in users if u.is_active]
    by_role: dict[str, int] = {}
    for u in active:
        by_role[u.role] = by_role.get(u.role, 0) + 1

    branches = db.query(Branch).filter(
        Branch.tenant_id == tenant_id, Branch.is_deleted == False,
    ).all()
    depts = db.query(Department).filter(
        Department.tenant_id == tenant_id, Department.is_deleted == False,
    ).all()

    lines = [
        "## Organisation",
        f"- Active team members: {len(active)} (total accounts: {len(users)})",
        f"- Role breakdown: {', '.join(f'{v} {k}' for k, v in by_role.items())}",
        f"- Branches ({len(branches)}): {', '.join(b.name for b in branches) or 'None'}",
        "",
        "### Departments",
    ]
    # Per-department headcount
    dept_users: dict[str, list] = {}
    for u in active:
        did = u.department_id or "__none__"
        dept_users.setdefault(did, []).append(u)
    for d in depts:
        members = dept_users.get(d.id, [])
        roles = {}
        for u in members:
            roles[u.role] = roles.get(u.role, 0) + 1
        branch_name = d.branch.name if d.branch else "—"
        lines.append(
            f"- {d.name} (Branch: {branch_name}): "
            f"{len(members)} members — "
            f"{', '.join(f'{v} {k}' for k, v in roles.items()) or 'empty'}"
        )
    unassigned = len(dept_users.get("__none__", []))
    if unassigned:
        lines.append(f"- (No department): {unassigned} members")

    return "\n".join(lines)


def _section_employees(db: Session, tenant_id: str, L: dict) -> str:
    """Full employee roster with role, department, manager, and 30-day KPIs."""
    from .analytics import get_employee_kpis

    employees = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.is_deleted == False,
    ).all()

    # Build manager name map
    mgr_map = {u.id: u.name for u in employees}

    lines = [f"## {L.get('Employees', 'Team Members')} Roster & Performance (last 30 days)"]
    for emp in employees:
        dept_name = emp.department.name if emp.department else "No dept"
        mgr_name  = mgr_map.get(emp.manager_id, "—") if emp.manager_id else "—"
        status    = "Active" if emp.is_active else "Inactive"
        try:
            kpis = get_employee_kpis(db, emp.id, tenant_id)
            perf = (
                f"closed={kpis['closed_30d']}, "
                f"active_tickets={kpis['active_count']}, "
                f"on_time={kpis['on_time_rate']}%, "
                f"checklist_compliance={kpis['compliance_rate']}%, "
                f"avg_tat={_h(kpis['avg_tat_hours'])}"
            )
        except Exception:
            perf = "no KPI data"
        lines.append(
            f"- {emp.name} | {emp.role} | {dept_name} | Reports to: {mgr_name} | {status} | {perf}"
        )
    if not employees:
        lines.append("  No employees on record yet.")
    return "\n".join(lines)


def _section_tickets(db: Session, tenant_id: str, L: dict) -> str:
    """Ticket/delegation snapshot — open tickets, overdue, team load, recent titles."""
    now = _now()
    since = _since(30)

    all_open = db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id, Ticket.is_deleted == False,
        Ticket.status.notin_(["CLOSED", "DONE"]),
    ).all()

    overdue  = [t for t in all_open if t.due_at and t.due_at < now]
    flagged  = [t for t in all_open if t.is_flagged]
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

    status_count: dict[str, int] = {}
    for t in all_open:
        status_count[t.status] = status_count.get(t.status, 0) + 1

    # Team load map
    load: dict[str, int] = {}
    load_names: dict[str, str] = {}
    for t in all_open:
        if t.current_assignee_id:
            load[t.current_assignee_id] = load.get(t.current_assignee_id, 0) + 1
    if load:
        members = db.query(User).filter(User.id.in_(list(load.keys()))).all()
        for u in members:
            load_names[u.id] = u.name

    sorted_load = sorted(load.items(), key=lambda x: x[1], reverse=True)[:8]
    unassigned = sum(1 for t in all_open if not t.current_assignee_id)

    lines = [
        f"## {L.get('Tickets', 'Tickets')} / Delegation Snapshot",
        f"- Open tickets: {len(all_open)}",
        f"  Status breakdown: {', '.join(f'{v} {k}' for k, v in status_count.items())}",
        f"- Overdue: {len(overdue)} ({_pct(len(overdue), len(all_open))} of open)",
        f"- Flagged: {len(flagged)}  |  CRITICAL: {len(critical)}  |  HIGH: {len(high)}",
        f"- Unassigned: {unassigned}",
        "",
        f"## Last 30 Days — Delegation Performance",
        f"- Tickets closed: {len(closed_30)}",
        f"- On-time closure: {on_time}/{len(closed_30)} ({_pct(on_time, len(closed_30))})",
        f"- Avg resolution time: {_h(org_avg_tat)}",
        "",
        "## Team Load (open tickets per person)",
    ] + [f"  - {load_names.get(uid, uid)}: {cnt} open" for uid, cnt in sorted_load]

    # Recent overdue ticket titles (max 10) for context
    if overdue:
        lines += ["", "## Overdue Ticket Titles (sample)"]
        for t in sorted(overdue, key=lambda x: x.due_at)[:10]:
            assignee = load_names.get(t.current_assignee_id, "Unassigned")
            lines.append(
                f"  - [{t.priority}] {t.title} — assigned to {assignee}, "
                f"due {t.due_at.strftime('%d %b') if t.due_at else '—'}"
            )

    return "\n".join(lines)


def _section_checklists(db: Session, tenant_id: str, L: dict) -> str:
    """Checklist compliance overview with per-template detail."""
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

    tmpls = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.tenant_id == tenant_id,
        ChecklistTemplate.is_deleted == False,
        ChecklistTemplate.is_active == True,
    ).all()

    freq_counts: dict[str, int] = {}
    for t in tmpls:
        freq_counts[t.frequency] = freq_counts.get(t.frequency, 0) + 1

    lines = [
        f"## {L.get('Checklists', 'Checklists')} (last 30 days)",
        f"- Active templates: {len(tmpls)}",
        f"- Assignments in period: {total}",
        f"- Completed: {done} ({_pct(done, total)})",
        f"- Overdue (currently): {overdue}",
        f"- Frequency breakdown: {', '.join(f'{v} {k}' for k, v in sorted(freq_counts.items(), key=lambda x: -x[1]))}",
        "",
        "### Checklist Templates (title | frequency | assigned to | compliance last 30d)",
    ]

    # Bulk per-template compliance
    tmpl_ids = [t.id for t in tmpls]
    if tmpl_ids:
        asgn_rows = db.query(
            ChecklistAssignment.template_id,
            ChecklistAssignment.status,
            func.count(ChecklistAssignment.id),
        ).filter(
            ChecklistAssignment.template_id.in_(tmpl_ids),
            ChecklistAssignment.due_at >= since,
            ChecklistAssignment.due_at <= now,
        ).group_by(ChecklistAssignment.template_id, ChecklistAssignment.status).all()

        tmpl_stats: dict[str, dict[str, int]] = {}
        for tid, status, cnt in asgn_rows:
            tmpl_stats.setdefault(tid, {})[status] = cnt

        for t in tmpls:
            s = tmpl_stats.get(t.id, {})
            t_total = sum(s.values())
            t_done  = s.get("DONE", 0)
            compliance = _pct(t_done, t_total) if t_total else "no data yet"

            if t.assigned_to_user_id and t.assigned_to_user:
                assignee = t.assigned_to_user.name
            elif t.assigned_to_dept_id and t.assigned_to_dept:
                assignee = f"All {t.assigned_to_role} in {t.assigned_to_dept.name}"
            else:
                assignee = f"All {t.assigned_to_role or 'EMPLOYEE'}"

            lines.append(f"- {t.title} | {t.frequency} | {assignee} | {compliance}")

    return "\n".join(lines)


def _section_fms(db: Session, tenant_id: str, L: dict) -> str:
    """FMS (Flow Management System) — flows, open tickets, stage distribution."""
    try:
        from .database import FMSFlow, FMSTicket, FMSStage
    except ImportError:
        return ""

    now = _now()
    since = _since(30)

    flows = db.query(FMSFlow).filter(
        FMSFlow.tenant_id == tenant_id,
        FMSFlow.is_deleted == False,
        FMSFlow.is_active == True,
    ).all()

    if not flows:
        return "## FMS (Flow Management System)\n- No active flows configured."

    all_tickets = db.query(FMSTicket).filter(
        FMSTicket.tenant_id == tenant_id,
        FMSTicket.is_deleted == False,
    ).all()

    open_tickets   = [t for t in all_tickets if t.status not in ("COMPLETED", "CLOSED")]
    closed_30      = [t for t in all_tickets if t.status in ("COMPLETED", "CLOSED") and t.created_at >= since]
    flagged        = [t for t in open_tickets if t.status == "FLAGGED"]
    on_hold        = [t for t in open_tickets if t.status == "ON_HOLD"]
    help_requested = [t for t in open_tickets if t.status == "HELP_REQUESTED"]

    lines = [
        "## FMS (Flow Management System)",
        f"- Active flows: {len(flows)}",
        f"- Open tickets: {len(open_tickets)}",
        f"- Completed in last 30 days: {len(closed_30)}",
        f"- Flagged: {len(flagged)}  |  On hold: {len(on_hold)}  |  Help requested: {len(help_requested)}",
        "",
        "### Flows & Per-Flow Ticket Count",
    ]

    # Stage name map
    all_stages = db.query(FMSStage).filter(
        FMSStage.tenant_id == tenant_id,
        FMSStage.is_deleted == False,
    ).all()
    stage_map = {s.id: s.name for s in all_stages}
    stage_flow_map = {s.id: s.flow_id for s in all_stages}

    for flow in flows:
        flow_tickets = [t for t in open_tickets if t.flow_id == flow.id]
        flow_stages = [s for s in all_stages if s.flow_id == flow.id]
        # Stage distribution for open tickets in this flow
        stage_dist: dict[str, int] = {}
        for t in flow_tickets:
            sname = stage_map.get(t.current_stage_id, "Unknown")
            stage_dist[sname] = stage_dist.get(sname, 0) + 1
        dist_str = ", ".join(f"{v} in '{k}'" for k, v in stage_dist.items()) if stage_dist else "none open"
        lines.append(
            f"- {flow.name}: {len(flow_tickets)} open tickets ({dist_str})"
        )
        lines.append(
            f"  Stages: {' → '.join(s.name for s in flow_stages)}"
        )

    # Open ticket titles (sample, up to 15)
    if open_tickets:
        lines += ["", "### Open FMS Ticket Titles (sample)"]
        for t in open_tickets[:15]:
            stage_name = stage_map.get(t.current_stage_id, "—")
            flow_name  = next((f.name for f in flows if f.id == t.flow_id), "—")
            lines.append(
                f"  - [{t.status}] {t.title} | Flow: {flow_name} | Stage: {stage_name}"
            )

    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────

def build_context(db: Session, tenant_id: str, L: dict) -> str:
    """
    Assemble a comprehensive text snapshot of the tenant's operational data.
    Returned as a string; injected into the system prompt for the AI assistant.
    """
    sections = [
        _section_org(db, tenant_id, L),
        _section_employees(db, tenant_id, L),
        _section_tickets(db, tenant_id, L),
        _section_checklists(db, tenant_id, L),
        _section_fms(db, tenant_id, L),
    ]
    header = (
        f"DATA SNAPSHOT — generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"{'─' * 60}\n"
    )
    return header + "\n\n".join(s for s in sections if s)
