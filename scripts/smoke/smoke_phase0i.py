"""Phase 0-I Feature Flag System Smoke Test"""
import sys, os, requests, time, subprocess, threading

BASE = "http://localhost:8000"
ok = []; fail = []

def check(label, cond, hint=""):
    if cond: print(f"  OK   {label}"); ok.append(label)
    else:    print(f"  FAIL {label}" + (f"  [{hint}]" if hint else "")); fail.append(label)

proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
py   = os.path.join(proj, "venv", "Scripts", "python.exe")
srv  = subprocess.Popen([py,"-m","uvicorn","app.main:app","--port","8000"],
    cwd=proj, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

ready = threading.Event()
def _watch():
    for line in srv.stdout:
        if b"Application startup complete" in line:
            ready.set(); return
threading.Thread(target=_watch, daemon=True).start()
ready.wait(timeout=20)

try:
    # ── Setup: create SA + factory ──────────────────────────────────────────
    sa = requests.Session()
    sa.post(BASE+"/superadmin/setup",
            data={"name":"SA","email":"sa@test.io","password":"pass123","confirm":"pass123"},
            allow_redirects=True)

    # Register factory (TRIAL)
    requests.post(BASE+"/register", data={
        "factory_name":"Flag Test Co","slug":"flagtest",
        "name":"Admin","phone":"0111000111","password":"pass1234"
    })

    # SA approves it on STARTER
    r = sa.get(BASE+"/superadmin/tenants")
    import re
    tids = re.findall(r'/superadmin/tenants/([a-f0-9\-]{36})', r.text)
    tid  = tids[0] if tids else None
    check("Tenant ID found", tid is not None)
    sa.post(BASE+f"/superadmin/tenants/{tid}/approve", data={"plan":"STARTER"}, allow_redirects=True)

    # Login as factory admin
    cl = requests.Session()
    cl.post(BASE+"/login", data={"slug":"flagtest","phone":"0111000111","password":"pass1234"},
            allow_redirects=True)

    # ── I-1: Plan page accessible ───────────────────────────────────────────
    print("\n[I-1] Tenant plan page")
    r = cl.get(BASE+"/plan")
    check("GET /plan -> 200", r.status_code == 200)
    check("Current plan shown", "Starter" in r.text)
    check("Usage limits shown", "Team Members" in r.text)
    check("Feature list rendered", "Ticket Management" in r.text)
    check("Locked features shown", "Professional" in r.text)
    check("Plan comparison table", "Plan Comparison" in r.text)
    check("Upgrade CTA present", "Upgrade to" in r.text)
    check("Plan nav link present", "/plan" in cl.get(BASE+"/dashboard").text)

    # ── I-2: Plan limits enforced — users ───────────────────────────────────
    print("\n[I-2] User limit enforcement (Starter = 10)")
    # Starter allows 10 users. Admin is already 1. Add 9 more to hit limit.
    for i in range(9):
        cl.post(BASE+"/employees/create",
                data={"name":f"Emp{i}","phone":f"099900{i:04d}","password":"pass"},
                allow_redirects=True)
    # 11th should be blocked
    r = cl.post(BASE+"/employees/create",
                data={"name":"Extra","phone":"0888888888","password":"pass"},
                allow_redirects=True)
    check("11th user blocked on Starter", "limit" in r.url.lower() or "limit" in r.text.lower())

    # ── I-3: SA feature override ─────────────────────────────────────────────
    print("\n[I-3] SA feature override")
    r = sa.get(BASE+f"/superadmin/tenants/{tid}/features")
    check("Feature flags page 200", r.status_code == 200)
    check("Feature catalog shown", "CSV Export" in r.text or "CSV" in r.text)
    check("Force ON button present", "Force ON" in r.text)
    check("Plan status shown", "Plan Status" in r.text)

    # Force-enable CSV_EXPORT (PROFESSIONAL feature) for STARTER tenant
    r = sa.post(BASE+f"/superadmin/tenants/{tid}/features/override",
                data={"feature":"CSV_EXPORT","action":"enable","note":"Trial unlock"},
                allow_redirects=True)
    check("Override saved", r.status_code == 200)
    check("SA badge shown", "SA" in r.text or "Override" in r.text or "saved" in r.url)

    # Tenant plan page should now show CSV_EXPORT as active
    r = cl.get(BASE+"/plan")
    check("CSV_EXPORT shows as Included after override", "Included" in r.text)
    check("Custom override note shown", "Custom override" in r.text)

    # Force-disable KANBAN (Starter feature) for this tenant
    r = sa.post(BASE+f"/superadmin/tenants/{tid}/features/override",
                data={"feature":"KANBAN","action":"disable","note":"Disabled for testing"},
                allow_redirects=True)
    check("Force-disable saved", r.status_code == 200)

    # Clear the KANBAN override
    r = sa.post(BASE+f"/superadmin/tenants/{tid}/features/override",
                data={"feature":"KANBAN","action":"clear"},
                allow_redirects=True)
    check("Override cleared", r.status_code == 200)

    # Clear all overrides
    r = sa.post(BASE+f"/superadmin/tenants/{tid}/features/clear-all", allow_redirects=True)
    check("Clear all overrides", r.status_code == 200)
    check("Cleared msg shown", "cleared" in r.url or "cleared" in r.text.lower())

    # ── I-4: Feature catalog completeness ────────────────────────────────────
    print("\n[I-4] Feature catalog")
    import sys; sys.path.insert(0, proj)
    from app.constants import FEATURE_CATALOG, PLAN_LIMITS, get_limit, within_limit, next_plan

    check("24 features defined", len(FEATURE_CATALOG) == 24)
    check("All 4 plan limits defined", len(PLAN_LIMITS) == 4)

    # Test helpers
    class FakeTenant:
        plan = "STARTER"
        id   = "fake"
    t = FakeTenant()
    check("get_limit STARTER users = 10", get_limit(t, "max_users") == 10)
    check("within_limit 9/10 = True",     within_limit(t, "max_users", 9))
    check("within_limit 10/10 = False",   not within_limit(t, "max_users", 10))
    check("get_limit STARTER branches = 1", get_limit(t, "max_branches") == 1)
    t.plan = "ENTERPRISE"
    check("ENTERPRISE users = None (unlimited)", get_limit(t, "max_users") is None)
    check("within_limit unlimited always True",  within_limit(t, "max_users", 9999))

    check("next_plan(STARTER) = PROFESSIONAL", next_plan("STARTER") == "PROFESSIONAL")
    check("next_plan(PROFESSIONAL) = ENTERPRISE", next_plan("PROFESSIONAL") == "ENTERPRISE")
    check("next_plan(ENTERPRISE) = None",     next_plan("ENTERPRISE") is None)

    # ── I-5: Plan upgrade unlocks features ────────────────────────────────────
    print("\n[I-5] Plan upgrade unlocks features")
    sa.post(BASE+f"/superadmin/tenants/{tid}/plan", data={"plan":"PROFESSIONAL"}, allow_redirects=True)
    r = cl.get(BASE+"/plan")
    check("After upgrade to PRO, plan page shows Professional", "Professional" in r.text)
    check("PRO features now Included", "Included" in r.text)

    print(f"\n{'='*52}")
    print(f"  PASSED {len(ok)}/{len(ok)+len(fail)}")
    if fail: print("  FAILED:", fail)
    print('='*52)

finally:
    srv.terminate(); srv.wait()

sys.exit(0 if not fail else 1)
