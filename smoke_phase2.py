"""
Phase 2 — FMS Core Smoke Test
Sub-phases 2-A through 2-F:
  2-A: DB models (6 tables)
  2-B: Flow builder (CRUD, library deploy, CSV import)
  2-C: Ticket lifecycle (create, transition, non-linear)
  2-D: Reassign, help request, flag, helper add/remove
  2-E: Dashboard & swimlane
  2-F: Analytics
"""
import sys, os, re, json, threading, subprocess, sqlite3, requests, time

BASE = "http://localhost:8000"
ok = []; fail = []

def check(label, cond, hint=""):
    if cond:  print(f"  OK   {label}"); ok.append(label)
    else:     print(f"  FAIL {label}" + (f"  [{hint}]" if hint else "")); fail.append(label)

proj = os.path.dirname(__file__)
py   = os.path.join(proj, "venv", "Scripts", "python.exe")
srv  = subprocess.Popen(
    [py, "-m", "uvicorn", "app.main:app", "--port", "8000"],
    cwd=proj, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
ready = threading.Event()
def _watch():
    for line in srv.stdout:
        if b"Application startup complete" in line:
            ready.set(); return
threading.Thread(target=_watch, daemon=True).start()
ready.wait(timeout=30)

try:
    # ── Setup ─────────────────────────────────────────────────────────────────
    sa = requests.Session()
    sa.post(BASE+"/superadmin/setup",
            data={"name":"SA","email":"sa@p2.io","password":"pass123","confirm":"pass123"},
            allow_redirects=True)
    requests.post(BASE+"/register", data={
        "factory_name":"P2 Factory","slug":"p2test",
        "name":"Admin","phone":"0200000001","password":"pass1234",
    })
    r = sa.get(BASE+"/superadmin/tenants")
    tids = re.findall(r'/superadmin/tenants/([a-f0-9\-]{36})', r.text)
    tid = tids[0] if tids else None
    check("Tenant created", tid is not None)
    sa.post(BASE+f"/superadmin/tenants/{tid}/approve",
            data={"plan":"PROFESSIONAL"}, allow_redirects=True)

    s = requests.Session()
    s.post(BASE+"/login",
           data={"slug":"p2test","phone":"0200000001","password":"pass1234"},
           allow_redirects=True)

    # Create an employee
    s.post(BASE+"/employees/create", data={
        "name":"Worker One","phone":"0299991111","password":"pass1234","role":"EMPLOYEE",
    })
    r = s.get(BASE+"/employees")
    emp_ids = re.findall(r'/employees/([a-f0-9\-]{36})', r.text)
    worker_id = emp_ids[0] if emp_ids else None
    check("Employee created", worker_id is not None)

    # ── 2-A: DB Models ─────────────────────────────────────────────────────────
    print("\n[2-A] FMS Database Models")
    conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    check("fms_flows table", "fms_flows" in tables)
    check("fms_stages table", "fms_stages" in tables)
    check("fms_tickets table", "fms_tickets" in tables)
    check("fms_stage_history table", "fms_stage_history" in tables)
    check("fms_events table", "fms_events" in tables)
    check("fms_ticket_helpers table", "fms_ticket_helpers" in tables)

    # Verify key columns
    ticket_cols = [c[1] for c in conn.execute("PRAGMA table_info(fms_tickets)").fetchall()]
    check("fms_tickets.status column", "status" in ticket_cols)
    check("fms_tickets.is_flagged column", "is_flagged" in ticket_cols)
    check("fms_tickets.wo_number column", "wo_number" in ticket_cols)

    history_cols = [c[1] for c in conn.execute("PRAGMA table_info(fms_stage_history)").fetchall()]
    check("fms_stage_history.direction column", "direction" in history_cols)
    check("fms_stage_history.from_stage_id column", "from_stage_id" in history_cols)
    check("fms_stage_history.qty_completed column", "qty_completed" in history_cols)

    stage_cols = [c[1] for c in conn.execute("PRAGMA table_info(fms_stages)").fetchall()]
    check("fms_stages.target_tat_hours column", "target_tat_hours" in stage_cols)
    check("fms_stages.is_terminal column", "is_terminal" in stage_cols)
    conn.close()

    # ── 2-B: Flow Builder ──────────────────────────────────────────────────────
    print("\n[2-B] Flow Builder")
    r = s.get(BASE+"/fms/flows")
    check("GET /fms/flows → 200", r.status_code == 200, f"got {r.status_code}")

    r = s.get(BASE+"/fms/flows/new")
    check("GET /fms/flows/new → 200", r.status_code == 200, f"got {r.status_code}")

    # Create a flow with 3 stages
    stages = [
        {"name":"Design","order":0,"color":"#3b82f6","target_tat_hours":4,
         "is_mandatory":True,"completion_note_required":False,"is_terminal":False},
        {"name":"Fabrication","order":1,"color":"#f59e0b","target_tat_hours":8,
         "is_mandatory":True,"completion_note_required":True,"is_terminal":False},
        {"name":"QC & Dispatch","order":2,"color":"#10b981","target_tat_hours":2,
         "is_mandatory":True,"completion_note_required":False,"is_terminal":True},
    ]
    r = s.post(BASE+"/fms/flows/new", data={
        "name":"Steel Production Flow","description":"Test flow",
        "color":"#3b82f6","is_active":"true",
        "stages_json": json.dumps(stages),
    }, allow_redirects=True)
    check("POST /fms/flows/new creates flow → 200", r.status_code == 200, f"got {r.status_code}")

    # Extract flow id from redirect URL or listing
    r2 = s.get(BASE+"/fms/flows")
    flow_ids = re.findall(r'/fms/flows/([a-f0-9\-]{36})', r2.text)
    flow_id = flow_ids[0] if flow_ids else None
    check("Flow created and listed", flow_id is not None)

    if flow_id:
        r = s.get(BASE+f"/fms/flows/{flow_id}")
        check("GET /fms/flows/{id} → 200", r.status_code == 200)
        check("Stage names in edit page", "Design" in r.text and "Fabrication" in r.text)

    # CSV import
    csv_data = "flow_name,description,color,stages\nTest CSV Flow,From CSV,#ef4444,Intake|Processing|Output\n"
    r = s.post(BASE+"/fms/flows/import-csv",
               files={"file": ("flows.csv", csv_data.encode(), "text/csv")},
               allow_redirects=True)
    check("POST /fms/flows/import-csv → 200", r.status_code == 200, f"got {r.status_code}")

    # Verify CSV flow was created
    r = s.get(BASE+"/fms/flows")
    check("CSV-imported flow appears in list", "Test CSV Flow" in r.text)

    # ── 2-C: Ticket Lifecycle ─────────────────────────────────────────────────
    print("\n[2-C] Ticket Lifecycle")
    r = s.get(BASE+"/fms/tickets/new")
    check("GET /fms/tickets/new → 200", r.status_code == 200, f"got {r.status_code}")

    # Get stage IDs from DB
    conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
    if flow_id:
        stage_rows = conn.execute(
            "SELECT id, name, \"order\" FROM fms_stages WHERE flow_id=? AND is_deleted=0 ORDER BY \"order\"",
            (flow_id,)).fetchall()
    else:
        stage_rows = []
    conn.close()

    check("Flow has 3 stages", len(stage_rows) == 3, f"got {len(stage_rows)}")
    stage_ids = [row[0] for row in stage_rows]
    s1_id = stage_ids[0] if len(stage_ids) > 0 else None
    s2_id = stage_ids[1] if len(stage_ids) > 1 else None
    s3_id = stage_ids[2] if len(stage_ids) > 2 else None

    ticket_id = None
    if flow_id and s1_id and worker_id:
        r = s.post(BASE+"/fms/tickets/new", data={
            "title":"PROD-001 Steel Frame","description":"Test ticket",
            "flow_id":flow_id,"starting_stage_id":s1_id,
            "priority":"HIGH","assignee_id":worker_id,
            "wo_number":"WO-0001","target_qty":"50","qty_unit":"pcs",
        }, allow_redirects=True)
        check("POST /fms/tickets/new → 200", r.status_code == 200, f"got {r.status_code}")

        # Get ticket id
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        row = conn.execute(
            "SELECT id FROM fms_tickets WHERE title='PROD-001 Steel Frame' LIMIT 1").fetchone()
        ticket_id = row[0] if row else None

        # Verify stage history created
        if ticket_id:
            h = conn.execute(
                "SELECT * FROM fms_stage_history WHERE ticket_id=?", (ticket_id,)).fetchall()
            check("Stage history created on ticket create", len(h) == 1)
            check("Initial stage history has FORWARD direction", h[0][8] == "FORWARD" if h else False)

            # Verify CREATED event logged
            ev = conn.execute(
                "SELECT event_type FROM fms_events WHERE ticket_id=?", (ticket_id,)).fetchall()
            event_types = [e[0] for e in ev]
            check("CREATED event logged", "CREATED" in event_types)
            check("STAGE_ENTERED event logged", "STAGE_ENTERED" in event_types)
        conn.close()

        check("Ticket id obtained", ticket_id is not None)

    if ticket_id:
        r = s.get(BASE+f"/fms/tickets/{ticket_id}")
        check("GET /fms/tickets/{id} → 200", r.status_code == 200, f"got {r.status_code}")
        check("Ticket title in detail page", "PROD-001 Steel Frame" in r.text)
        check("Stage pipeline in detail page", "Design" in r.text)

    # 2-C-2: Forward transition
    if ticket_id and s2_id and worker_id:
        r = s.post(BASE+f"/fms/tickets/{ticket_id}/transition", data={
            "next_stage_id":s2_id,"new_assignee_id":worker_id,
            "completion_note":"Design complete","qty_completed":"10",
        }, allow_redirects=True)
        check("Forward transition → 200", r.status_code == 200, f"got {r.status_code}")

        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        t = conn.execute("SELECT current_stage_id FROM fms_tickets WHERE id=?", (ticket_id,)).fetchone()
        check("Ticket current_stage_id updated", t[0] == s2_id if t else False)

        h2 = conn.execute(
            "SELECT * FROM fms_stage_history WHERE ticket_id=? ORDER BY entered_at",
            (ticket_id,)).fetchall()
        check("2 stage history rows after transition", len(h2) == 2, f"got {len(h2)}")
        check("First stage history exited_at set", h2[0][6] is not None if len(h2)>=1 else False)
        check("qty_completed recorded", h2[0][10] == 10 if len(h2)>=1 else False, str(h2[0] if h2 else ''))
        conn.close()

    # 2-C-4: Backward transition (non-linear)
    if ticket_id and s1_id and worker_id:
        r = s.post(BASE+f"/fms/tickets/{ticket_id}/transition", data={
            "next_stage_id":s1_id,"new_assignee_id":worker_id,
            "return_reason":"Design needs revision","qty_completed":"0",
        }, allow_redirects=True)
        check("Backward transition → 200", r.status_code == 200, f"got {r.status_code}")

        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        h3 = conn.execute(
            "SELECT direction FROM fms_stage_history WHERE ticket_id=? ORDER BY entered_at",
            (ticket_id,)).fetchall()
        dirs = [h[0] for h in h3]
        check("3 stage history rows (non-linear revisit)", len(h3) == 3, f"got {len(h3)}: {dirs}")
        check("Third row is BACKWARD direction", dirs[-1] == "BACKWARD" if dirs else False)
        conn.close()

    # 2-C-5: Non-linear: go forward again (creates 4th row)
    if ticket_id and s2_id and worker_id:
        r = s.post(BASE+f"/fms/tickets/{ticket_id}/transition", data={
            "next_stage_id":s2_id,"new_assignee_id":worker_id,
            "completion_note":"Revised design complete","qty_completed":"15",
        }, allow_redirects=True)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        h4 = conn.execute(
            "SELECT count(*) FROM fms_stage_history WHERE ticket_id=?", (ticket_id,)).fetchone()
        check("4th row created on revisit (non-linear immutable log)", h4[0] == 4, f"got {h4[0]}")
        conn.close()

    # 2-C terminal: move to terminal stage → COMPLETED
    if ticket_id and s3_id and worker_id:
        r = s.post(BASE+f"/fms/tickets/{ticket_id}/transition", data={
            "next_stage_id":s3_id,"new_assignee_id":worker_id,
            "completion_note":"All fabrication done","qty_completed":"50",
        }, allow_redirects=True)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        t = conn.execute("SELECT status FROM fms_tickets WHERE id=?", (ticket_id,)).fetchone()
        check("Ticket status = COMPLETED on terminal stage", t[0] == "COMPLETED" if t else False)
        conn.close()

    # ── 2-D: Actions ──────────────────────────────────────────────────────────
    print("\n[2-D] Ticket Actions")

    # Create a fresh ticket for action tests
    conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
    t2 = conn.execute("SELECT id FROM fms_tickets WHERE title='PROD-001 Steel Frame'").fetchone()
    action_ticket_id = t2[0] if t2 else None
    conn.close()

    # Create a new ticket for action tests
    if flow_id and s1_id and worker_id:
        r = s.post(BASE+"/fms/tickets/new", data={
            "title":"ACTION-TEST Ticket","description":"For action tests",
            "flow_id":flow_id,"starting_stage_id":s1_id,
            "priority":"MEDIUM","assignee_id":worker_id,
        }, allow_redirects=True)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        row = conn.execute(
            "SELECT id FROM fms_tickets WHERE title='ACTION-TEST Ticket' LIMIT 1").fetchone()
        action_ticket_id = row[0] if row else None
        conn.close()

    if action_ticket_id:
        # Comment
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"comment","comment":"This is a test comment"},
                   allow_redirects=True)
        check("Action: comment → 200", r.status_code == 200)

        # Flag
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"flag","flag_reason":"Quality issue spotted"},
                   allow_redirects=True)
        check("Action: flag → 200", r.status_code == 200)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        t = conn.execute("SELECT is_flagged, flagged_reason FROM fms_tickets WHERE id=?",
                         (action_ticket_id,)).fetchone()
        check("Ticket is_flagged = True", t[0] == 1 if t else False)
        check("flagged_reason stored", "Quality issue" in (t[1] or '') if t else False)
        conn.close()

        # Unflag
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"unflag"}, allow_redirects=True)
        check("Action: unflag → 200", r.status_code == 200)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        t = conn.execute("SELECT is_flagged FROM fms_tickets WHERE id=?", (action_ticket_id,)).fetchone()
        check("Ticket is_flagged = False after unflag", t[0] == 0 if t else False)
        conn.close()

        # Help request
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"help_request","comment":"Machine broke down"},
                   allow_redirects=True)
        check("Action: help_request → 200", r.status_code == 200)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        t = conn.execute("SELECT status FROM fms_tickets WHERE id=?", (action_ticket_id,)).fetchone()
        check("Ticket status = HELP_REQUESTED", t[0] == "HELP_REQUESTED" if t else False)
        conn.close()

        # Add helper
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"add_helper","helper_id":worker_id,"reason":"Extra support"},
                   allow_redirects=True)
        check("Action: add_helper → 200", r.status_code == 200)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        h = conn.execute("SELECT * FROM fms_ticket_helpers WHERE ticket_id=?",
                         (action_ticket_id,)).fetchall()
        check("Helper row created", len(h) >= 1, f"got {len(h)}")
        conn.close()

        # Reassign
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"reassign","new_assignee_id":worker_id,"reason":"Shift handoff"},
                   allow_redirects=True)
        check("Action: reassign → 200", r.status_code == 200)

        # On hold
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"on_hold","reason":"Waiting for parts"},
                   allow_redirects=True)
        check("Action: on_hold → 200", r.status_code == 200)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        t = conn.execute("SELECT status FROM fms_tickets WHERE id=?", (action_ticket_id,)).fetchone()
        check("Ticket status = ON_HOLD", t[0] == "ON_HOLD" if t else False)
        conn.close()

        # Resume
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"resume"}, allow_redirects=True)
        check("Action: resume → 200", r.status_code == 200)

        # Close
        r = s.post(BASE+f"/fms/tickets/{action_ticket_id}/action",
                   data={"action":"close","reason":"Cancelled by client"},
                   allow_redirects=True)
        check("Action: close → 200", r.status_code == 200)
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        t = conn.execute("SELECT status FROM fms_tickets WHERE id=?", (action_ticket_id,)).fetchone()
        check("Ticket status = CLOSED", t[0] == "CLOSED" if t else False)
        conn.close()

    # Event log completeness
    if action_ticket_id:
        conn = sqlite3.connect(os.path.join(proj, "factoryos.db"))
        ev = [r[0] for r in conn.execute(
            "SELECT event_type FROM fms_events WHERE ticket_id=?", (action_ticket_id,)).fetchall()]
        conn.close()
        check("COMMENT event logged", "COMMENT" in ev)
        check("FLAGGED event logged", "FLAGGED" in ev)
        check("UNFLAGGED event logged", "UNFLAGGED" in ev)
        check("HELP_REQUESTED event logged", "HELP_REQUESTED" in ev)
        check("REASSIGNED event logged", "REASSIGNED" in ev)
        check("CLOSED event logged", "CLOSED" in ev)

    # ── 2-E: Dashboard & Swimlane ─────────────────────────────────────────────
    print("\n[2-E] Dashboard & Swimlane")
    r = s.get(BASE+"/fms/dashboard")
    check("GET /fms/dashboard → 200", r.status_code == 200, f"got {r.status_code}")
    check("Summary strip: active_tickets", "Active Tickets" in r.text)
    check("Summary strip: TaT Breaches", "TaT Breaches" in r.text)
    check("Summary strip: Flagged", "Flagged" in r.text)
    check("Summary strip: Compliance", "Compliance" in r.text)
    check("Flow tab in dashboard", "Steel Production Flow" in r.text)
    check("Swimlane board present", "swimlane" in r.text or "kanban" in r.text.lower())

    if flow_id:
        r = s.get(BASE+f"/fms/dashboard?flow_id={flow_id}")
        check("Dashboard with flow_id → 200", r.status_code == 200)
        check("Stage columns shown", "Design" in r.text and "Fabrication" in r.text)

    r = s.get(BASE+"/fms/tickets/new")
    check("New ticket form → 200", r.status_code == 200)

    # ── 2-F: Analytics ────────────────────────────────────────────────────────
    print("\n[2-F] Analytics")
    r = s.get(BASE+"/fms/analytics")
    check("GET /fms/analytics → 200", r.status_code == 200, f"got {r.status_code}")
    check("Analytics: My Compliance Rate", "My Compliance Rate" in r.text)
    check("Analytics: Compliance by Flow", "Compliance by Flow" in r.text)
    check("Analytics: Professional section shown (PROFESSIONAL plan)", "Employee TaT Analysis" in r.text)

    # Unauthenticated access should redirect
    r2 = requests.get(BASE+"/fms/dashboard", allow_redirects=False)
    check("Unauthenticated /fms/dashboard redirects", r2.status_code in (302, 303, 307))

    # ── Nav ───────────────────────────────────────────────────────────────────
    print("\n[Nav] FMS nav link")
    r = s.get(BASE+"/dashboard")
    check("'Flow Board' nav link in base.html", "Flow Board" in r.text)

    print(f"\n{'='*52}")
    print(f"  PASSED {len(ok)}/{len(ok)+len(fail)}")
    if fail: print("  FAILED:", fail)
    print('='*52)

finally:
    srv.terminate(); srv.wait()

sys.exit(0 if not fail else 1)
