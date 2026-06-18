"""
KPI & analytics calculation engine — Phase 0-E-9, 0-G-1 through 0-G-11
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import or_ as _or
from .database import Ticket, ChecklistAssignment, ChecklistTemplate, User, Department


def _resolve_filter_uids(db, tenant_id, dept_ids=None, manager_ids=None):
    """Return list of user IDs matching any selected dept or manager, or None for no filter."""
    if not dept_ids and not manager_ids:
        return None
    q = db.query(User).filter(User.tenant_id==tenant_id, User.is_deleted==False)
    conds = []
    if dept_ids:
        # Expand dept names: collect all dept IDs with the same name (cross-branch)
        names = [d.name for d in db.query(Department).filter(
            Department.id.in_(dept_ids), Department.tenant_id==tenant_id).all()]
        all_dept_ids = [d.id for d in db.query(Department).filter(
            Department.tenant_id==tenant_id, Department.name.in_(names),
            Department.is_deleted==False).all()]
        conds.append(User.department_id.in_(all_dept_ids))
    if manager_ids:
        conds.append(User.id.in_(manager_ids))
        conds.append(User.manager_id.in_(manager_ids))
    q = q.filter(_or(*conds))
    return [u.id for u in q.all()]


def calc_tat_hours(ticket) -> float | None:
    """Turnaround time for a ticket from creation to close/done."""
    if not ticket.created_at:
        return None
    end = ticket.closed_at
    if not end and ticket.status in ("DONE", "CLOSED"):
        end = datetime.utcnow()
    if not end:
        return None
    return max((end - ticket.created_at).total_seconds() / 3600, 0)


def get_org_avg_tat(db: Session, tenant_id: str) -> float:
    """Org-wide average TaT (hours) for the last 30 days — Phase 0-E-9."""
    since = datetime.utcnow() - timedelta(days=30)
    closed = db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.is_deleted == False,
        Ticket.status.in_(["DONE", "CLOSED"]),
        Ticket.created_at >= since,
    ).all()
    tats = [calc_tat_hours(t) for t in closed]
    tats = [x for x in tats if x is not None]
    return round(sum(tats) / len(tats), 1) if tats else 0.0


def get_employee_kpis(db: Session, user_id: str, tenant_id: str) -> dict:
    """
    Full KPI set for a single employee — Phase 0-E-6, 0-G-1 to 0-G-5.
    Returns a dict ready to pass into the template context.
    """
    now = datetime.utcnow()
    since_30 = now - timedelta(days=30)

    # ── Closed tickets last 30 days ──────────────────────────────────────────
    closed = db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.current_assignee_id == user_id,
        Ticket.is_deleted == False,
        Ticket.status.in_(["DONE", "CLOSED"]),
        Ticket.created_at >= since_30,
    ).all()

    tats = [calc_tat_hours(t) for t in closed]
    tats = [x for x in tats if x is not None]
    avg_tat = round(sum(tats) / len(tats), 1) if tats else 0.0

    on_time = sum(
        1 for t in closed
        if t.due_at and (t.closed_at or now) <= t.due_at
    )
    on_time_rate = round(on_time / len(closed) * 100) if closed else 0

    # ── Active tickets by priority ────────────────────────────────────────────
    active = db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.current_assignee_id == user_id,
        Ticket.is_deleted == False,
        Ticket.status.notin_(["CLOSED", "DONE"]),
    ).all()
    by_priority = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for t in active:
        by_priority[t.priority] = by_priority.get(t.priority, 0) + 1

    # ── Checklist compliance last 30 days ────────────────────────────────────
    total_cl = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == tenant_id,
        ChecklistAssignment.user_id == user_id,
        ChecklistAssignment.due_at >= since_30,
        ChecklistAssignment.due_at <= now,
    ).count()
    done_cl = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id == tenant_id,
        ChecklistAssignment.user_id == user_id,
        ChecklistAssignment.status == "DONE",
        ChecklistAssignment.due_at >= since_30,
        ChecklistAssignment.due_at <= now,
    ).count()
    compliance_rate = round(done_cl / total_cl * 100) if total_cl else 0

    # ── 8-week compliance trend ───────────────────────────────────────────────
    weekly_compliance = []
    weekly_labels = []
    for i in range(7, -1, -1):
        w_start = now - timedelta(weeks=i + 1)
        w_end = now - timedelta(weeks=i)
        wt = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tenant_id,
            ChecklistAssignment.user_id == user_id,
            ChecklistAssignment.due_at >= w_start,
            ChecklistAssignment.due_at < w_end,
        ).count()
        wd = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id == tenant_id,
            ChecklistAssignment.user_id == user_id,
            ChecklistAssignment.status == "DONE",
            ChecklistAssignment.due_at >= w_start,
            ChecklistAssignment.due_at < w_end,
        ).count()
        weekly_compliance.append(round(wd / wt * 100) if wt else 100)
        weekly_labels.append(w_start.strftime("%d %b"))

    # ── Acknowledgement response time ─────────────────────────────────────────
    acked = db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.current_assignee_id == user_id,
        Ticket.acknowledged_at != None,
        Ticket.created_at >= since_30,
    ).all()
    ack_times = [
        (t.acknowledged_at - t.created_at).total_seconds() / 3600
        for t in acked
        if t.acknowledged_at and t.created_at
    ]
    avg_ack_hours = round(sum(ack_times) / len(ack_times), 1) if ack_times else 0.0

    return {
        "avg_tat_hours":      avg_tat,
        "on_time_rate":       on_time_rate,
        "active_count":       len(active),
        "active_by_priority": by_priority,
        "compliance_rate":    compliance_rate,
        "weekly_compliance":  weekly_compliance,
        "weekly_labels":      weekly_labels,
        "avg_ack_hours":      avg_ack_hours,
        "closed_30d":         len(closed),
    }


def get_all_employee_kpis(db: Session, tenant_id: str) -> list:
    """KPIs for all employees — Phase 0-E-7 / 0-E-8."""
    employees = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.is_deleted == False,
        User.is_active == True,
    ).all()
    result = []
    for emp in employees:
        kpis = get_employee_kpis(db, emp.id, tenant_id)
        kpis["user"] = emp
        result.append(kpis)
    # Sort by compliance rate ascending (worst first) for admin bar chart
    result.sort(key=lambda x: x["compliance_rate"])
    return result


def get_ticket_volume_chart(db: Session, tenant_id: str) -> dict:
    """Created vs closed ticket counts per week (last 8 weeks) — Phase 0-E-12."""
    now = datetime.utcnow()
    labels, created_counts, closed_counts = [], [], []
    for i in range(7, -1, -1):
        w_start = now - timedelta(weeks=i + 1)
        w_end = now - timedelta(weeks=i)
        c = db.query(Ticket).filter(
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted == False,
            Ticket.created_at >= w_start,
            Ticket.created_at < w_end,
        ).count()
        d = db.query(Ticket).filter(
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted == False,
            Ticket.status.in_(["DONE", "CLOSED"]),
            Ticket.closed_at >= w_start,
            Ticket.closed_at < w_end,
        ).count()
        labels.append(w_start.strftime("%d %b"))
        created_counts.append(c)
        closed_counts.append(d)
    return {"labels": labels, "created": created_counts, "closed": closed_counts}


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard redesign analytics — new functions (dashboard v2)
# ══════════════════════════════════════════════════════════════════════════════

from .database import Department, FMSFlow, FMSTicket, FMSStage, ChecklistTemplate


def _date_bounds(date_from: str = None, date_to: str = None):
    """Return (start, end) datetimes from explicit date strings.
    Defaults: last 30 days. Accepts 'YYYY-MM-DD' strings."""
    now = datetime.utcnow()
    try:
        end = datetime.strptime(date_to, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59) if date_to else now
    except (ValueError, TypeError):
        end = now
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d") if date_from else end - timedelta(days=30)
    except (ValueError, TypeError):
        start = end - timedelta(days=30)
    return start, end


# ── Delegation scorecards ─────────────────────────────────────────────────────

def get_delegation_scorecards(db: Session, tenant_id: str,
                               date_from: str = None, date_to: str = None,
                               dept_ids: list = None,
                               manager_ids: list = None) -> dict:
    start, now = _date_bounds(date_from, date_to)
    _uids = _resolve_filter_uids(db, tenant_id, dept_ids, manager_ids)

    def _scope(q):
        if _uids is not None:
            q = q.filter(Ticket.current_assignee_id.in_(_uids))
        return q

    base = lambda: _scope(db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id, Ticket.is_deleted == False))

    open_count = base().filter(Ticket.status.notin_(["CLOSED","DONE"])).count()
    closed = base().filter(
        Ticket.status.in_(["DONE","CLOSED"]),
        Ticket.created_at >= start).all()
    tats = [x for x in [calc_tat_hours(t) for t in closed] if x is not None]
    avg_tat = round(sum(tats)/len(tats), 1) if tats else 0.0
    due_24h = base().filter(
        Ticket.status.notin_(["CLOSED","DONE"]),
        Ticket.due_at >= now,
        Ticket.due_at <= now + timedelta(hours=24)).all()

    return {
        "open": open_count,
        "closed": len(closed),
        "avg_tat": avg_tat,
        "org_avg_tat": get_org_avg_tat(db, tenant_id),
        "due_24h_count": len(due_24h),
        "due_24h_tickets": [
            {"id": t.id, "title": t.title,
             "assignee": t.current_assignee.name if t.current_assignee else "—",
             "due_at": t.due_at.strftime("%d %b, %H:%M") if t.due_at else "—",
             "priority": t.priority}
            for t in sorted(due_24h, key=lambda x: x.due_at or datetime.max)
        ],
    }


def get_delegation_weekly(db: Session, tenant_id: str,
                           dept_ids: list = None, manager_ids: list = None) -> dict:
    now = datetime.utcnow()
    labels, created_list, closed_list = [], [], []
    uids = _resolve_filter_uids(db, tenant_id, dept_ids, manager_ids)
    for i in range(7, -1, -1):
        w0 = now - timedelta(weeks=i+1)
        w1 = now - timedelta(weeks=i)
        def fq(extra):
            q = db.query(Ticket).filter(
                Ticket.tenant_id==tenant_id, Ticket.is_deleted==False)
            if uids: q = q.filter(Ticket.current_assignee_id.in_(uids))
            return q.filter(*extra).count()
        labels.append(w0.strftime("%d %b"))
        created_list.append(fq([Ticket.created_at>=w0, Ticket.created_at<w1]))
        closed_list.append(fq([Ticket.status.in_(["DONE","CLOSED"]),
                                Ticket.closed_at>=w0, Ticket.closed_at<w1]))
    return {"labels": labels, "created": created_list, "closed": closed_list}


def get_delegation_by_dept(db: Session, tenant_id: str, date_from: str = None, date_to: str = None) -> list:
    start, now = _date_bounds(date_from, date_to)
    result = []
    for d in db.query(Department).filter(
            Department.tenant_id==tenant_id, Department.is_deleted==False).all():
        uids = [u.id for u in db.query(User).filter(
            User.tenant_id==tenant_id, User.department_id==d.id,
            User.is_deleted==False).all()]
        if not uids: continue
        def q(): return db.query(Ticket).filter(
            Ticket.tenant_id==tenant_id, Ticket.is_deleted==False,
            Ticket.current_assignee_id.in_(uids))
        result.append({
            "dept": d.name,
            "open":    q().filter(Ticket.status.notin_(["CLOSED","DONE"])).count(),
            "closed":  q().filter(Ticket.status.in_(["DONE","CLOSED"]),
                                   Ticket.created_at>=start).count(),
            "overdue": q().filter(Ticket.status.notin_(["CLOSED","DONE"]),
                                   Ticket.due_at<now).count(),
        })
    return result


def get_delegation_by_manager(db: Session, tenant_id: str, date_from: str = None, date_to: str = None) -> list:
    start, _ = _date_bounds(date_from, date_to)
    result = []
    for m in db.query(User).filter(
            User.tenant_id==tenant_id, User.role.in_(["ADMIN","MANAGER"]),
            User.is_deleted==False).all():
        rids = [u.id for u in db.query(User).filter(
            User.manager_id==m.id, User.is_deleted==False).all()]
        if not rids: continue
        result.append({
            "manager": m.name,
            "open":   db.query(Ticket).filter(Ticket.tenant_id==tenant_id,
                Ticket.is_deleted==False, Ticket.current_assignee_id.in_(rids),
                Ticket.status.notin_(["CLOSED","DONE"])).count(),
            "closed": db.query(Ticket).filter(Ticket.tenant_id==tenant_id,
                Ticket.is_deleted==False, Ticket.current_assignee_id.in_(rids),
                Ticket.status.in_(["DONE","CLOSED"]),
                Ticket.created_at>=start).count(),
        })
    return result


def get_delegation_by_priority(db: Session, tenant_id: str,
                                dept_ids: list = None, manager_ids: list = None) -> dict:
    q = db.query(Ticket).filter(Ticket.tenant_id==tenant_id,
        Ticket.is_deleted==False, Ticket.status.notin_(["CLOSED","DONE"]))
    scoped_uids = _resolve_filter_uids(db, tenant_id, dept_ids, manager_ids)
    if scoped_uids is not None:
        q = q.filter(Ticket.current_assignee_id.in_(scoped_uids))
    counts = {"CRITICAL":0,"HIGH":0,"MEDIUM":0,"LOW":0}
    for t in q.all(): counts[t.priority] = counts.get(t.priority,0)+1
    return counts


def get_employee_tat_ranking(db: Session, tenant_id: str, date_from: str = None, date_to: str = None,
                              dept_ids: list = None, manager_ids: list = None) -> list:
    start, _ = _date_bounds(date_from, date_to)
    org_avg = get_org_avg_tat(db, tenant_id)
    scoped_uids = _resolve_filter_uids(db, tenant_id, dept_ids, manager_ids)
    eq = db.query(User).filter(User.tenant_id==tenant_id,
        User.is_deleted==False, User.is_active==True)
    if scoped_uids is not None:
        eq = eq.filter(User.id.in_(scoped_uids))
    result = []
    for emp in eq.all():
        closed = db.query(Ticket).filter(Ticket.tenant_id==tenant_id,
            Ticket.is_deleted==False, Ticket.current_assignee_id==emp.id,
            Ticket.status.in_(["DONE","CLOSED"]), Ticket.created_at>=start).all()
        tats = [x for x in [calc_tat_hours(t) for t in closed] if x is not None]
        avg = round(sum(tats)/len(tats),1) if tats else None
        result.append({
            "name": emp.name,
            "dept": emp.department.name if emp.department else "—",
            "closed": len(closed),
            "avg_tat": avg,
            "vs_org": ("better" if avg is not None and avg < org_avg
                       else "slower" if avg is not None and avg > org_avg*1.2
                       else "avg"),
        })
    result.sort(key=lambda x: (x["avg_tat"] is None, x["avg_tat"] or 999))
    return result


# ── Checklist scorecards ──────────────────────────────────────────────────────

def get_checklist_scorecards(db: Session, tenant_id: str, date_from: str = None, date_to: str = None,
                              dept_ids: list = None, manager_ids: list = None) -> dict:
    start, now = _date_bounds(date_from, date_to)
    distinct = db.query(ChecklistTemplate).filter(
        ChecklistTemplate.tenant_id==tenant_id, ChecklistTemplate.is_active==True,
        ChecklistTemplate.is_deleted==False).count()

    uids = _resolve_filter_uids(db, tenant_id, dept_ids, manager_ids)

    # Only include assignments whose template still exists (not deleted)
    _active_tmpl_ids = [t.id for t in db.query(ChecklistTemplate.id).filter(
        ChecklistTemplate.tenant_id==tenant_id,
        ChecklistTemplate.is_deleted==False).all()]

    def cl(extra=[]):
        q = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id==tenant_id,
            ChecklistAssignment.is_deleted==False,
            ChecklistAssignment.template_id.in_(_active_tmpl_ids),
            ChecklistAssignment.due_at>=start, ChecklistAssignment.due_at<=now)
        if uids: q = q.filter(ChecklistAssignment.user_id.in_(uids))
        for f in extra: q = q.filter(f)
        return q

    total = cl().count()
    done  = cl([ChecklistAssignment.status=="DONE"]).count()
    compliance = round(done/total*100) if total else 100

    done_items = cl([ChecklistAssignment.status=="DONE",
                     ChecklistAssignment.completed_at!=None]).all()
    tats = [abs((a.completed_at-a.due_at).total_seconds()/3600)
            for a in done_items if a.completed_at and a.due_at]
    avg_tat = round(sum(tats)/len(tats),1) if tats else 0.0

    all_done = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id==tenant_id,
        ChecklistAssignment.is_deleted==False,
        ChecklistAssignment.template_id.in_(_active_tmpl_ids),
        ChecklistAssignment.status=="DONE",
        ChecklistAssignment.completed_at!=None,
        ChecklistAssignment.due_at>=now-timedelta(days=30)).all()
    org_tats = [abs((a.completed_at-a.due_at).total_seconds()/3600)
                for a in all_done if a.completed_at and a.due_at]
    org_avg = round(sum(org_tats)/len(org_tats),1) if org_tats else 0.0

    due_q = db.query(ChecklistAssignment).filter(
        ChecklistAssignment.tenant_id==tenant_id,
        ChecklistAssignment.is_deleted==False,
        ChecklistAssignment.template_id.in_(_active_tmpl_ids),
        ChecklistAssignment.status.in_(["PENDING","IN_PROGRESS"]),
        ChecklistAssignment.due_at>=now,
        ChecklistAssignment.due_at<=now+timedelta(hours=24))
    if uids: due_q = due_q.filter(ChecklistAssignment.user_id.in_(uids))
    due_24h = due_q.all()

    return {
        "distinct_templates": distinct,
        "compliance": compliance,
        "avg_tat": avg_tat,
        "org_avg_tat": org_avg,
        "due_24h_count": len(due_24h),
        "due_24h_items": [
            {"title": a.template.title if a.template else "—",
             "assignee": a.user.name if a.user else "—",
             "due_at": a.due_at.strftime("%d %b, %H:%M") if a.due_at else "—"}
            for a in sorted(due_24h, key=lambda x: x.due_at or datetime.max)
        ],
    }


def get_checklist_weekly(db: Session, tenant_id: str,
                          dept_ids: list = None, manager_ids: list = None) -> dict:
    now = datetime.utcnow()
    uids = _resolve_filter_uids(db, tenant_id, dept_ids, manager_ids)
    labels, rates = [], []
    for i in range(7,-1,-1):
        w0 = now-timedelta(weeks=i+1); w1 = now-timedelta(weeks=i)
        q = db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id==tenant_id,
            ChecklistAssignment.due_at>=w0, ChecklistAssignment.due_at<w1)
        if uids: q = q.filter(ChecklistAssignment.user_id.in_(uids))
        total = q.count()
        done  = q.filter(ChecklistAssignment.status=="DONE").count()
        rates.append(round(done/total*100) if total else 0)
        labels.append(w0.strftime("%d %b"))
    return {"labels": labels, "rates": rates}


def get_checklist_by_template(db: Session, tenant_id: str, date_from: str = None, date_to: str = None) -> list:
    start, now = _date_bounds(date_from, date_to)
    result = []
    for t in db.query(ChecklistTemplate).filter(
            ChecklistTemplate.tenant_id==tenant_id, ChecklistTemplate.is_active==True,
            ChecklistTemplate.is_deleted==False).all():
        def q(): return db.query(ChecklistAssignment).filter(
            ChecklistAssignment.template_id==t.id,
            ChecklistAssignment.due_at>=start, ChecklistAssignment.due_at<=now)
        total = q().count()
        done  = q().filter(ChecklistAssignment.status=="DONE").count()
        result.append({"title": t.title, "frequency": t.frequency,
                        "assigned": total, "done": done,
                        "rate": round(done/total*100) if total else 100})
    result.sort(key=lambda x: x["rate"])
    return result


def get_checklist_by_dept(db: Session, tenant_id: str, date_from: str = None, date_to: str = None) -> list:
    start, now = _date_bounds(date_from, date_to)
    result = []
    for d in db.query(Department).filter(
            Department.tenant_id==tenant_id, Department.is_deleted==False).all():
        uids = [u.id for u in db.query(User).filter(
            User.tenant_id==tenant_id, User.department_id==d.id,
            User.is_deleted==False).all()]
        if not uids: continue
        def q(): return db.query(ChecklistAssignment).filter(
            ChecklistAssignment.tenant_id==tenant_id,
            ChecklistAssignment.user_id.in_(uids),
            ChecklistAssignment.due_at>=start, ChecklistAssignment.due_at<=now)
        total = q().count()
        done  = q().filter(ChecklistAssignment.status=="DONE").count()
        result.append({"dept": d.name, "total": total, "done": done,
                        "rate": round(done/total*100) if total else 100})
    return result


# ── FMS scorecards ────────────────────────────────────────────────────────────

def get_fms_scorecards(db: Session, tenant_id: str, date_from: str = None, date_to: str = None) -> dict:
    from .database import FMSStageHistory
    start, now = _date_bounds(date_from, date_to)

    flows = db.query(FMSFlow).filter(FMSFlow.tenant_id==tenant_id,
        FMSFlow.is_active==True, FMSFlow.is_deleted==False).all()
    total_active = db.query(FMSTicket).filter(FMSTicket.tenant_id==tenant_id,
        FMSTicket.is_deleted==False,
        FMSTicket.status.notin_(["COMPLETED","CLOSED"])).count()

    visits = db.query(FMSStageHistory).join(
        FMSTicket, FMSStageHistory.ticket_id==FMSTicket.id).filter(
        FMSTicket.tenant_id==tenant_id, FMSStageHistory.exited_at!=None,
        FMSStageHistory.entered_at>=start).all()
    tats = [(v.exited_at-v.entered_at).total_seconds()/3600
            for v in visits if v.exited_at and v.entered_at]
    avg_stage_tat = round(sum(tats)/len(tats),1) if tats else 0.0

    all_v = db.query(FMSStageHistory).join(
        FMSTicket, FMSStageHistory.ticket_id==FMSTicket.id).filter(
        FMSTicket.tenant_id==tenant_id, FMSStageHistory.exited_at!=None,
        FMSStageHistory.entered_at>=now-timedelta(days=30)).all()
    org_tats = [(v.exited_at-v.entered_at).total_seconds()/3600
                for v in all_v if v.exited_at and v.entered_at]
    org_avg = round(sum(org_tats)/len(org_tats),1) if org_tats else 0.0

    due_24h = db.query(FMSTicket).filter(FMSTicket.tenant_id==tenant_id,
        FMSTicket.is_deleted==False,
        FMSTicket.status.notin_(["COMPLETED","CLOSED"]),
        FMSTicket.due_at>=now,
        FMSTicket.due_at<=now+timedelta(hours=24)).all()

    return {
        "distinct_flows": len(flows),
        "total_active": total_active,
        "avg_stage_tat": avg_stage_tat,
        "org_avg_stage_tat": org_avg,
        "due_24h_count": len(due_24h),
        "due_24h_tickets": [
            {"id": t.id, "title": t.title,
             "flow": t.flow.name if t.flow else "—",
             "stage": t.current_stage.name if t.current_stage else "—",
             "assignee": t.current_assignee.name if t.current_assignee else "—",
             "due_at": t.due_at.strftime("%d %b, %H:%M") if t.due_at else "—",
             "priority": t.priority}
            for t in sorted(due_24h, key=lambda x: x.due_at or datetime.max)
        ],
    }


def get_fms_flow_summary(db: Session, tenant_id: str) -> list:
    from .database import FMSStageHistory
    now = datetime.utcnow()
    result = []
    for flow in db.query(FMSFlow).filter(FMSFlow.tenant_id==tenant_id,
            FMSFlow.is_active==True, FMSFlow.is_deleted==False).all():
        def tq(extra=[]): return db.query(FMSTicket).filter(
            FMSTicket.flow_id==flow.id, FMSTicket.is_deleted==False, *extra)
        active    = tq([FMSTicket.status.notin_(["COMPLETED","CLOSED"])]).count()
        completed = tq([FMSTicket.status.in_(["COMPLETED","CLOSED"])]).count()
        flagged   = tq([FMSTicket.is_flagged==True,
                        FMSTicket.status.notin_(["COMPLETED","CLOSED"])]).count()
        visits = db.query(FMSStageHistory).join(
            FMSTicket, FMSStageHistory.ticket_id==FMSTicket.id).filter(
            FMSTicket.flow_id==flow.id, FMSStageHistory.exited_at!=None).all()
        tats = [(v.exited_at-v.entered_at).total_seconds()/3600
                for v in visits if v.exited_at and v.entered_at]
        avg_tat = round(sum(tats)/len(tats),1) if tats else 0.0
        active_v = db.query(FMSStageHistory).join(
            FMSTicket, FMSStageHistory.ticket_id==FMSTicket.id).filter(
            FMSTicket.flow_id==flow.id, FMSStageHistory.exited_at==None).all()
        breaches = sum(1 for v in active_v
                       if v.stage_id and (s:=db.query(FMSStage).get(v.stage_id))
                       and s.target_tat_hours
                       and (now-v.entered_at).total_seconds()/3600 > s.target_tat_hours)
        result.append({"id": flow.id, "name": flow.name,
                        "color": flow.color or "#3b82f6",
                        "active": active, "completed": completed,
                        "flagged": flagged, "avg_stage_tat": avg_tat,
                        "breaches": breaches})
    return result


def get_fms_stage_breakdown(db: Session, flow_id: str, tenant_id: str) -> list:
    from .database import FMSStageHistory
    now = datetime.utcnow()
    result = []
    for stage in db.query(FMSStage).filter(
            FMSStage.flow_id==flow_id, FMSStage.is_deleted==False
            ).order_by(FMSStage.order).all():
        tickets = db.query(FMSTicket).filter(
            FMSTicket.flow_id==flow_id,
            FMSTicket.current_stage_id==stage.id,
            FMSTicket.is_deleted==False,
            FMSTicket.status.notin_(["COMPLETED","CLOSED"])).all()
        times = []
        for t in tickets:
            h = db.query(FMSStageHistory).filter(
                FMSStageHistory.ticket_id==t.id,
                FMSStageHistory.stage_id==stage.id,
                FMSStageHistory.exited_at==None
            ).order_by(FMSStageHistory.entered_at.desc()).first()
            if h: times.append((now-h.entered_at).total_seconds()/3600)
        avg = round(sum(times)/len(times),1) if times else None
        oldest = round(max(times),1) if times else None
        breaches = sum(1 for h in times
                       if stage.target_tat_hours and h > stage.target_tat_hours)
        result.append({"name": stage.name,
                        "target_tat": stage.target_tat_hours,
                        "ticket_count": len(tickets),
                        "avg_time_hrs": avg,
                        "oldest_hrs": oldest,
                        "breaches": breaches})
    return result


def get_fms_weekly(db: Session, tenant_id: str) -> dict:
    now = datetime.utcnow()
    labels, created_list, completed_list = [], [], []
    for i in range(7,-1,-1):
        w0 = now-timedelta(weeks=i+1); w1 = now-timedelta(weeks=i)
        def q(): return db.query(FMSTicket).filter(
            FMSTicket.tenant_id==tenant_id, FMSTicket.is_deleted==False)
        labels.append(w0.strftime("%d %b"))
        created_list.append(q().filter(
            FMSTicket.created_at>=w0, FMSTicket.created_at<w1).count())
        completed_list.append(q().filter(
            FMSTicket.status.in_(["COMPLETED","CLOSED"]),
            FMSTicket.completed_at>=w0, FMSTicket.completed_at<w1).count())
    return {"labels": labels, "created": created_list, "completed": completed_list}
