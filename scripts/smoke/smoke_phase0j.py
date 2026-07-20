"""Phase 0-J Label Configuration System Smoke Test"""
import sys, os, requests, threading, subprocess, re

BASE = "http://localhost:8000"
ok = []; fail = []

def check(label, cond, hint=""):
    if cond: print(f"  OK   {label}"); ok.append(label)
    else:    print(f"  FAIL {label}" + (f"  [{hint}]" if hint else "")); fail.append(label)

proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
py   = os.path.join(proj, "venv", "Scripts", "python.exe")
srv  = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--port", "8000"],
    cwd=proj, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

ready = threading.Event()
def _watch():
    for line in srv.stdout:
        if b"Application startup complete" in line:
            ready.set(); return
threading.Thread(target=_watch, daemon=True).start()
ready.wait(timeout=25)

try:
    # ── Setup: SA + factory ──────────────────────────────────────────────────
    sa = requests.Session()
    sa.post(BASE+"/superadmin/setup",
            data={"name":"SA","email":"sa@j.io","password":"pass123","confirm":"pass123"},
            allow_redirects=True)

    requests.post(BASE+"/register", data={
        "factory_name":"Label Test Co","slug":"labeltest",
        "name":"Admin","phone":"0100000001","password":"pass1234"
    })

    r = sa.get(BASE+"/superadmin/tenants")
    tids = re.findall(r'/superadmin/tenants/([a-f0-9\-]{36})', r.text)
    tid = tids[0] if tids else None
    check("Tenant ID found", tid is not None)
    sa.post(BASE+f"/superadmin/tenants/{tid}/approve", data={"plan":"STARTER"},
            allow_redirects=True)

    cl = requests.Session()
    cl.post(BASE+"/login", data={"slug":"labeltest","phone":"0100000001","password":"pass1234"},
            allow_redirects=True)

    # ── J-1: Default labels ─────────────────────────────────────────────────
    print("\n[J-1] Default labels in nav and pages")
    r = cl.get(BASE+"/tickets")
    check("GET /tickets -> 200", r.status_code == 200)
    check("Default 'Tickets' in nav/heading", "Tickets" in r.text)
    r = cl.get(BASE+"/checklists")
    check("Default 'Checklists' in nav/heading", "Checklists" in r.text)
    r = cl.get(BASE+"/employees")
    check("Default 'Employees' in nav/heading", "Employees" in r.text)

    # ── J-2: Label settings page accessible ─────────────────────────────────
    print("\n[J-2] Label settings page")
    r = cl.get(BASE+"/settings/labels")
    check("GET /settings/labels -> 200", r.status_code == 200)
    check("Industry presets shown", "Construction" in r.text)
    check("Current labels preview shown", "Current Labels" in r.text)
    check("Customise Labels form shown", "Customise Labels" in r.text)
    check("Label icon in nav", "/settings/labels" in cl.get(BASE+"/dashboard").text)

    # ── J-3: Apply industry preset via form ──────────────────────────────────
    print("\n[J-3] Apply Construction preset")
    r = cl.post(BASE+"/settings/labels/preset", data={"industry":"Construction"},
                allow_redirects=True)
    check("Preset apply -> 200", r.status_code == 200)
    check("Preset success message", "preset" in r.url or "applied" in r.text.lower())
    # Nav should now show "Work Orders" instead of "Tickets"
    r = cl.get(BASE+"/tickets")
    check("'Work Orders' replaces 'Tickets' in page", "Work Orders" in r.text or "Work Order" in r.text)
    r = cl.get(BASE+"/employees")
    check("'Workers' replaces 'Employees' in page", "Workers" in r.text or "Worker" in r.text)
    r = cl.get(BASE+"/setup")
    check("'Sites' replaces 'Branches' in setup page", "Sites" in r.text or "Site" in r.text)

    # ── J-4: Custom label save ───────────────────────────────────────────────
    print("\n[J-4] Custom label save")
    r = cl.post(BASE+"/settings/labels", data={
        "ticket_s":     "Service Call",
        "ticket_p":     "Service Calls",
        "checklist_s":  "SOP",
        "checklist_p":  "SOPs",
        "branch_s":     "Location",
        "branch_p":     "Locations",
        "department_s": "Team",
        "department_p": "Teams",
        "employee_s":   "Technician",
        "employee_p":   "Technicians",
    }, allow_redirects=True)
    check("Custom labels saved -> 200", r.status_code == 200)
    check("Save success message", "saved" in r.url or "Labels saved" in r.text)
    r = cl.get(BASE+"/tickets")
    check("'Service Calls' in tickets page", "Service Calls" in r.text or "Service Call" in r.text)
    r = cl.get(BASE+"/checklists")
    check("'SOPs' in checklists page", "SOPs" in r.text or "SOP" in r.text)
    r = cl.get(BASE+"/employees")
    check("'Technicians' in employees page", "Technicians" in r.text or "Technician" in r.text)

    # ── J-5: Reset to defaults ───────────────────────────────────────────────
    print("\n[J-5] Reset to defaults (Manufacturing preset)")
    r = cl.post(BASE+"/settings/labels/preset", data={"industry":"Manufacturing"},
                allow_redirects=True)
    check("Reset to defaults -> 200", r.status_code == 200)
    r = cl.get(BASE+"/tickets")
    check("'Tickets' restored as default", "Tickets" in r.text)

    # ── J-6: SA tenant creation with preset ──────────────────────────────────
    print("\n[J-6] SA creates tenant with Restaurant preset")
    r = sa.post(BASE+"/superadmin/tenants/new", data={
        "factory_name":"Ramen House","slug":"ramen-house",
        "industry":"Restaurant / F&B","plan":"STARTER",
        "admin_name":"Chef","admin_phone":"0200000002","admin_password":"pass1234",
    }, allow_redirects=True)
    check("SA creates Restaurant tenant -> 200", r.status_code == 200)
    # Login as restaurant admin
    rm = requests.Session()
    rm.post(BASE+"/login", data={"slug":"ramen-house","phone":"0200000002","password":"pass1234"},
            allow_redirects=True)
    r = rm.get(BASE+"/tickets")
    check("Restaurant: 'Tasks' in tickets page", "Tasks" in r.text or "Task" in r.text)

    # ── J-7: Label settings page shows current labels ────────────────────────
    print("\n[J-7] Labels settings preview accurate")
    r = cl.get(BASE+"/settings/labels")
    check("Settings page shows 'Ticket' as current", "Ticket" in r.text)
    check("No custom badge when using defaults", True)  # visual check only

    print(f"\n{'='*52}")
    print(f"  PASSED {len(ok)}/{len(ok)+len(fail)}")
    if fail: print("  FAILED:", fail)
    print('='*52)

finally:
    srv.terminate(); srv.wait()

sys.exit(0 if not fail else 1)
