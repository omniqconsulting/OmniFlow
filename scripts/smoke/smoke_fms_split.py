"""
FMS Split Engine — runtime confirmation smoke test.

Seeds a 5-stage flow (split-enabled at stage 2 & stage 4) directly via the
ORM, starts the real server against the same sqlite DB, then drives the
actual HTTP endpoints (create ticket, transition, manual split, merge,
backward move) exactly as a user/browser would, and asserts on both the
HTML responses and the resulting DB rows.

Run: venv\\Scripts\\python.exe smoke_fms_split.py
"""
import os, sys, json, threading, subprocess, time
import requests

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(PROJ, "venv", "Scripts", "python.exe")
BASE = "http://localhost:8010"

ok = []; fail = []
def check(label, cond, hint=""):
    if cond: print(f"  OK   {label}"); ok.append(label)
    else:    print(f"  FAIL {label}" + (f"  [{hint}]" if hint else "")); fail.append(label)

sys.path.insert(0, PROJ)
os.environ.setdefault("PORT", "8010")

from app.database import (
    SessionLocal, create_tables, Tenant, User, FMSFlow, FMSStage,
    FMSTicket, FMSTicketSplit, FMSStageHistory,
)
from app.auth import hash_password, create_token

create_tables()
db = SessionLocal()

# ── Seed tenant / users ──────────────────────────────────────────────────────
import uuid as _uuid
_slug = "splittest-smoke-" + _uuid.uuid4().hex[:8]
tenant = Tenant(name="Split Test Co", slug=_slug, plan="STARTER", is_approved=True)
db.add(tenant); db.flush()

admin = User(tenant_id=tenant.id, name="Admin", phone="9000000001",
             password_hash=hash_password("pass1234"), role="ADMIN")
emp1 = User(tenant_id=tenant.id, name="Employee One", phone="9000000002",
            password_hash=hash_password("pass1234"), role="EMPLOYEE")
db.add_all([admin, emp1]); db.flush()

# ── Seed a 5-stage flow: split-enabled at stage index 1 (Issue) and 3 (Receive) ──
flow = FMSFlow(tenant_id=tenant.id, name="Procurement Flow", is_active=True,
                closing_rule_json=None)
db.add(flow); db.flush()

REQ_FIELD = "req_qty"     # ticket-level custom field: Requested Quantity
ISSUE_FIELD = "issued_qty"
RECV_FIELD = "received_qty"

stage_defs = [
    dict(name="Request Raised", order=0, is_terminal=False),
    dict(name="Issue",          order=1, is_terminal=False,
         split_enabled=True, split_target_field=REQ_FIELD, split_actual_field=ISSUE_FIELD,
         custom_fields_json=json.dumps([{"id": ISSUE_FIELD, "label": "Issued Quantity", "field_type": "number", "required": True}])),
    dict(name="In Transit",     order=2, is_terminal=False),
    dict(name="Receive",        order=3, is_terminal=False,
         split_enabled=True, split_target_field=REQ_FIELD, split_actual_field=RECV_FIELD,
         custom_fields_json=json.dumps([{"id": RECV_FIELD, "label": "Actually Received Quantity", "field_type": "number", "required": True}])),
    dict(name="Closed",         order=4, is_terminal=True),
]
stages = []
for sd in stage_defs:
    s = FMSStage(tenant_id=tenant.id, flow_id=flow.id, target_tat_hours=24, **sd)
    db.add(s); stages.append(s)
db.flush()

db.commit()

# Snapshot plain-string ids before closing the session (ORM instances
# expire/detach on close — everything below the server launch uses these
# ids, never the ORM objects themselves).
class _Id:
    def __init__(self, id_, name=None): self.id = id_; self.name = name
tenant_id = tenant.id
admin_id, admin_role = admin.id, admin.role
emp1_id = emp1.id
flow_id = flow.id
S_REQUEST, S_ISSUE, S_TRANSIT, S_RECEIVE, S_CLOSED = [
    _Id(s.id, s.name) for s in stages
]
db.close()

# ── Launch the real server against the same DB ──────────────────────────────
env = dict(os.environ)
env["PORT"] = "8010"
env["DATABASE_URL"] = ""  # force sqlite default (same omniflow.db the ORM import above just wrote to)
env.pop("DATABASE_URL")
srv = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8010"],
                        cwd=PROJ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
ready = threading.Event()
log_lines = []
def _watch():
    for line in srv.stdout:
        log_lines.append(line)
        if b"Application startup complete" in line:
            ready.set()
threading.Thread(target=_watch, daemon=True).start()
started = ready.wait(timeout=30)
if not started:
    # Fallback: the banner line can race the reader thread on a fast box —
    # poll the actual HTTP port instead of trusting the log scrape.
    for _ in range(30):
        try:
            requests.get(BASE + "/login", timeout=1)
            started = True
            break
        except requests.exceptions.ConnectionError:
            time.sleep(1)

def _db():
    return SessionLocal()

try:
    check("Server started", started, b"".join(log_lines[-20:]).decode(errors="replace"))

    sess = requests.Session()
    token = create_token(admin_id, tenant_id, admin_role)
    sess.cookies.set("token", token)

    # ── 1. Create ticket: target_qty=100, requested qty custom field=100 ────
    print("\n[1] Create ticket at stage 1 (Request Raised)")
    r = sess.post(BASE + "/fms/tickets/new", data={
        "title": "PO-1001 Steel Rods", "flow_id": flow_id,
        "starting_stage_id": S_REQUEST.id, "priority": "MEDIUM",
        "assignee_id": emp1_id, "target_qty": "100", "qty_unit": "kg",
    }, allow_redirects=True)
    check("Ticket creation redirected/ok", r.status_code == 200)

    d = _db()
    ticket = d.query(FMSTicket).filter(FMSTicket.flow_id == flow_id).order_by(FMSTicket.created_at.desc()).first()
    check("Ticket exists", ticket is not None)
    ticket_id = ticket.id
    ticket_display_id = ticket.display_id or ticket_id[:8]
    # Set the ticket-level "Requested Quantity" custom field the split engine
    # falls back to for target_val (mirrors a ticket-creation-form field).
    ticket.ticket_custom_fields_json = json.dumps({REQ_FIELD: "100"})
    d.commit(); d.close()

    def get_splits(active_only=True):
        d = _db()
        q = d.query(FMSTicketSplit).filter(FMSTicketSplit.ticket_id == ticket_id)
        if active_only:
            q = q.filter(FMSTicketSplit.is_deleted == False)
        rows = q.order_by(FMSTicketSplit.created_at).all()
        out = [(s.id, s.split_label, s.split_display_id, s.qty, s.current_stage_id,
                s.parent_split_id, s.is_remainder, s.status) for s in rows]
        d.close()
        return out

    def stage_name(sid):
        d = _db(); s = d.query(FMSStage).get(sid); n = s.name if s else None; d.close(); return n

    def transition(split_id, next_stage_id, **extra):
        data = {"next_stage_id": next_stage_id, "new_assignee_id": emp1_id,
                "completion_note": "auto-test", "qty_completed": "0"}
        data.update(extra)
        if split_id:
            data["split_id"] = split_id
        return sess.post(f"{BASE}/fms/tickets/{ticket_id}/transition", data=data, allow_redirects=False)

    # ── 2. Move ticket forward stage0 -> stage1 (Issue) ──────────────────────
    print("\n[2] Move ticket: Request Raised -> Issue")
    r = transition(None, S_ISSUE.id)
    check("Transition to Issue -> redirect", r.status_code in (302, 303, 200), r.text[:300])
    splits = get_splits()
    check("Still exactly 1 split (no shortfall yet)", len(splits) == 1, splits)
    s1_id = splits[0][0]

    # ── 3. At Issue stage, enter issued_qty=60 (< requested 100) -> SPLIT ────
    print("\n[3] Enter Issued Quantity=60 (< 100 requested) -> expect auto-split")
    r = transition(s1_id, S_TRANSIT.id, **{"cf__" + ISSUE_FIELD: "60"})
    check("Transition Issue -> In Transit -> redirect", r.status_code in (302, 303), r.text[:300])
    splits = get_splits()
    check("Now 2 active splits (bullet 3: remainder + moved-forward)", len(splits) == 2, splits)
    remainder = next(s for s in splits if s[6])   # is_remainder
    moved = next(s for s in splits if not s[6])
    check("Remainder (S1) stayed at Issue stage", stage_name(remainder[4]) == "Issue", stage_name(remainder[4]))
    check("Remainder qty == 40 (100-60)", remainder[3] == 40, remainder)
    check("Moved split qty == 60", moved[3] == 60, moved)
    check("Moved split is at In Transit", stage_name(moved[4]) == "In Transit", stage_name(moved[4]))
    check("Moved split's parent is the remainder (lineage)", moved[5] == remainder[0], (moved[5], remainder[0]))
    check("Moved split got a hierarchical split_display_id", bool(moved[2]) and "-" in moved[2], moved[2])
    s1_remainder_id, s2_moved_id = remainder[0], moved[0]

    # ── 4. Dashboard Stage view shows the "N splits" badge ───────────────────
    print("\n[4] Dashboard Stage view shows split badge")
    r = sess.get(BASE + f"/fms/dashboard?view=stage&flow_id={flow_id}&stage_id={S_ISSUE.id}")
    check("Stage view 200", r.status_code == 200)
    _badge_ok = "2 splits" in r.text
    if not _badge_ok:
        idx = r.text.find("⑂")
        snippet = repr(r.text[max(0, idx-80):idx+80]) if idx >= 0 else "no split-glyph found at all"
        print("    DEBUG badge snippet:", snippet.encode("ascii", "backslashreplace").decode())
    check("Splits badge '2 splits' present", _badge_ok, None)

    # ── 5. Dashboard Timeline view shows ticket at BOTH Issue and In Transit ─
    print("\n[5] Timeline view shows the ticket at both stages simultaneously (the fixed gap)")
    r = sess.get(BASE + f"/fms/dashboard?view=timeline&flow_id={flow_id}")
    check("Timeline view 200", r.status_code == 200)
    # crude structural check: ticket's display id block appears once per
    # stage section it has a live split in (Issue AND In Transit)
    occurrences = r.text.count(ticket_display_id)
    check("Ticket appears >= 2 times in timeline (once per occupied stage)", occurrences >= 2, occurrences)
    check("Split badge visible in timeline card", "⑂" in r.text, None)

    # ── 6. Move the MOVED split (S2) forward to Receive stage ───────────────
    print("\n[6] Move S2 (60) forward: In Transit -> Receive")
    r = transition(s2_moved_id, S_RECEIVE.id)
    check("Transition -> redirect", r.status_code in (302, 303), r.text[:300])

    # ── 7. At Receive, enter received_qty=40 (< requested 100) -> NESTED split
    print("\n[7] Enter Received Quantity=40 on S2 (target still 100) -> expect nested split")
    r = transition(s2_moved_id, S_CLOSED.id, **{"cf__" + RECV_FIELD: "40"})
    check("Transition Receive -> Closed -> redirect", r.status_code in (302, 303), r.text[:300])
    splits = get_splits()
    check("Now 3 active leaf splits (S1@Issue, S2-remainder@Receive, S3@Closed)", len(splits) == 3, splits)
    by_id = {s[0]: s for s in splits}
    s3 = next(s for s in splits if s[5] == s2_moved_id)  # parent == S2
    check("S3's parent_split_id == S2 (nested lineage, bullet 4)", s3[5] == s2_moved_id, s3)
    check("S3 moved to Closed (terminal)", stage_name(s3[4]) == "Closed", stage_name(s3[4]))
    s2_after = by_id[s2_moved_id]
    check("S2 remains as remainder at Receive with qty 60", s2_after[4] and stage_name(s2_after[4]) == "Receive" and s2_after[3] == 60, s2_after)
    check("S3 got a 2-level hierarchical display id (parent's id + suffix)",
          s3[2] and by_id[s2_moved_id][2] and s3[2].startswith(by_id[s2_moved_id][2] + "-"), (s3[2], by_id[s2_moved_id][2]))
    s3_id = s3[0]

    # ── 8. Backward move rejected with a short reason (both endpoints) ──────
    print("\n[8] Backward move of S3 (Closed -> Receive) with a too-short reason must be rejected")
    r = transition(s3_id, S_RECEIVE.id, return_reason="bad")
    check("Short reason rejected (400/redirect-with-error)", r.status_code == 400 or "error" in r.text.lower() or r.status_code in (302, 303) and "err" in (r.headers.get("location") or ""),
          (r.status_code, r.headers.get("location")))

    print("[8b] Backward move of S3 with a valid (>=5 char) reason succeeds (manager override, since S3 already COMPLETED at the terminal stage)")
    r = transition(s3_id, S_RECEIVE.id, return_reason="rework needed on batch", is_override="true")
    check("Valid-reason backward move accepted", r.status_code in (302, 303), r.text[:300])
    splits = get_splits()
    s3_back = next(s for s in splits if s[0] == s3_id)
    check("S3 is back at Receive stage", stage_name(s3_back[4]) == "Receive", stage_name(s3_back[4]))

    # move it forward again to Closed for the rest of the scenarios
    r = transition(s3_id, S_CLOSED.id, **{"cf__" + RECV_FIELD: "999"})
    check("S3 forward to Closed again", r.status_code in (302, 303), r.text[:300])

    # ── 9. Manual "Split & move" (fms_split_ticket) on S1 (still at Issue) ───
    print("\n[9] Manual split: carve 15 of S1's 40 remaining qty from Issue -> In Transit")
    r = sess.post(BASE + f"/fms/tickets/{ticket_id}/splits/{s1_remainder_id}/split", data={
        "qty_to_move": "15", "target_stage_id": S_TRANSIT.id,
        "new_assignee_id": emp1_id, "completion_note": "manual carve",
        "cf__" + ISSUE_FIELD: "25",  # Issue stage's required field, same rule as a normal transition
    }, allow_redirects=False)
    check("Manual split -> redirect", r.status_code in (302, 303), r.text[:300])
    splits = get_splits()
    check("Now 4 active leaf splits", len(splits) == 4, splits)
    s1_after = next(s for s in splits if s[0] == s1_remainder_id)
    check("S1 reduced to 25 remaining (40-15)", s1_after[3] == 25, s1_after)
    # S2 also has parent_split_id == S1 (from the very first auto-split), so
    # filter it out explicitly rather than just excluding S1 itself.
    s4_manual = next(s for s in splits if s[5] == s1_remainder_id and s[0] not in (s1_remainder_id, s2_moved_id))
    check("Manual split S4 has hierarchical display id inherited from S1 (lineage fix)",
          bool(s4_manual[2]) and "-" in s4_manual[2], s4_manual[2])
    s4_id = s4_manual[0]

    print("[9b] Manual split backward move with short reason must also be rejected (consistency fix)")
    r = sess.post(BASE + f"/fms/tickets/{ticket_id}/splits/{s4_id}/split", data={
        "qty_to_move": "5", "target_stage_id": S_ISSUE.id, "return_reason": "no",
    }, allow_redirects=False)
    check("Manual-split short backward reason rejected", r.status_code == 400, (r.status_code, r.text[:200]))

    # ── 10. Merge S1 (25 @ Issue) and S4 (15 @ In Transit) back together ─────
    print("\n[10] Merge S1 + S4 (manager-only)")
    print("    pre-merge splits:", get_splits())
    print("    merging ids:", s1_remainder_id, s4_id)
    r = sess.post(BASE + f"/fms/tickets/{ticket_id}/splits/merge", data={
        "split_ids": [s1_remainder_id, s4_id], "reason": "consolidating after rework decision",
    }, allow_redirects=False)
    check("Merge -> redirect", r.status_code in (302, 303), r.text[:300])
    splits = get_splits()
    check("3 active leaf splits after merge", len(splits) == 3, splits)
    # Furthest-along of {S1 @ Issue(order1), S4 @ In Transit(order2)} is S4 —
    # it survives IN PLACE (same row/id) and absorbs S1's qty; S1 is retired.
    d = _db()
    s1_row = d.query(FMSTicketSplit).get(s1_remainder_id)
    s4_row = d.query(FMSTicketSplit).get(s4_id)
    check("S1 (less advanced) retired (is_deleted) after merge", s1_row.is_deleted is True, s1_row.is_deleted)
    check("S4 (furthest-along) survives, not retired", s4_row.is_deleted is False, s4_row.is_deleted)
    check("Survivor S4 absorbed combined qty 40 (25+15), still at In Transit",
          s4_row.qty == 40 and s4_row.current_stage_id == S_TRANSIT.id, (s4_row.qty, s4_row.current_stage_id))
    d.close()

    # ── 11. Aggregate close: ticket must NOT be COMPLETED while any leaf is open
    print("\n[11] Ticket status stays non-COMPLETED while any split is still open")
    d = _db(); t = d.query(FMSTicket).get(ticket_id); status_mid = t.status; d.close()
    check("Ticket not COMPLETED yet (still 3 open leaf splits)", status_mid != "COMPLETED", status_mid)

    # Drive every remaining leaf split to the terminal stage.
    print("[11b] Drive remaining leaf splits to Closed")
    splits = get_splits()
    for s in splits:
        sid, label, disp, qty, stage_id, parent, is_rem, status = s
        if stage_name(stage_id) == "Closed":
            continue
        # walk forward one stage at a time to Closed
        cur = stage_id
        order = {S_REQUEST.id: 0, S_ISSUE.id: 1, S_TRANSIT.id: 2, S_RECEIVE.id: 3, S_CLOSED.id: 4}
        path = [S_ISSUE, S_TRANSIT, S_RECEIVE, S_CLOSED]
        remaining_path = [st for st in path if order[st.id] > order[cur]]
        for nxt in remaining_path:
            # The required custom field belongs to the stage being EXITED
            # (cur), not the one being entered (nxt) — e.g. leaving Receive
            # for Closed is what needs "Actually Received Quantity".
            extra = {}
            if cur == S_RECEIVE.id:
                extra["cf__" + RECV_FIELD] = "999"  # over-deliver: no further split (R5)
            elif cur == S_ISSUE.id:
                extra["cf__" + ISSUE_FIELD] = "999"
            r = transition(sid, nxt.id, **extra)
            cur = nxt.id
            if r.status_code not in (302, 303):
                break
            # a further split may have been created (R2/R3) — refresh id to
            # whichever active split now sits furthest along this lineage
            cand = [x for x in get_splits() if x[0] == sid or x[5] == sid]
            live = [x for x in cand if x[0] == sid]
            if not live:
                # sid got consumed into a moved split — follow it
                moved_cands = [x for x in get_splits() if x[5] == sid and x[4] == nxt.id]
                if moved_cands:
                    sid = moved_cands[0][0]

    # terminal-complete the ones that landed on Closed but aren't COMPLETED yet
    for s in get_splits():
        sid, label, disp, qty, stage_id, parent, is_rem, status = s
        if stage_name(stage_id) == "Closed" and status != "COMPLETED":
            sess.post(f"{BASE}/fms/tickets/{ticket_id}/transition", data={
                "next_stage_id": "", "split_id": sid, "qty_completed": "0",
            }, allow_redirects=False)

    d = _db()
    final_splits = d.query(FMSTicketSplit).filter(
        FMSTicketSplit.ticket_id == ticket_id, FMSTicketSplit.is_deleted == False).all()
    all_terminal = all(s.status in ("COMPLETED", "CLOSED") for s in final_splits)
    t = d.query(FMSTicket).get(ticket_id)
    final_status = t.status
    final_rows = [(s.split_label, s.split_display_id, s.qty, s.status,
                   (s.current_stage.name if s.current_stage else None)) for s in final_splits]
    d.close()
    check("All leaf splits reached terminal status", all_terminal, final_rows)
    check("Aggregate rollup: ticket.status == COMPLETED once every leaf is terminal (bullet 7)",
          final_status == "COMPLETED", final_status)

    print("\nFinal split tree:")
    for row in final_rows:
        print("   ", row)

finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

print(f"\n{'='*60}\n{len(ok)} passed, {len(fail)} failed\n{'='*60}")
if fail:
    print("FAILED:")
    for f in fail:
        print("  -", f)
    sys.exit(1)
