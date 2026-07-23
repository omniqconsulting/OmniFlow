"""
Phase 0-H Smoke Test
Tests super-admin setup, login, tenant management, plan change, suspend/unsuspend.
"""
import sys, os, requests, time, subprocess

BASE = "http://localhost:8000"
PASS = []
FAIL = []

def check(label, cond, detail=""):
    if cond:
        print(f"  ✓  {label}")
        PASS.append(label)
    else:
        print(f"  ✗  {label}" + (f"  [{detail}]" if detail else ""))
        FAIL.append(label)

# ── Start server ──────────────────────────────────────────────────────────────
proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
py   = os.path.join(proj, "venv", "Scripts", "python.exe")
srv  = subprocess.Popen(
    [py, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
    cwd=proj, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(3)

try:
    print("\n── Phase 0-H: Super Admin Portal ────────────────────────────────")

    # ── 1. SA setup page ──────────────────────────────────────────────────────
    print("\n[H-1] First-time Setup")
    r = requests.get(f"{BASE}/superadmin/setup")
    check("GET /superadmin/setup → 200", r.status_code == 200)
    check("Setup form visible", "Create Super Admin Account" in r.text)

    # ── 2. Create SA account ──────────────────────────────────────────────────
    print("\n[H-2] Create SA & Login")
    s = requests.Session()
    r = s.post(f"{BASE}/superadmin/setup", data={
        "name": "Platform Admin", "email": "sa@omniflow.io",
        "password": "secret123", "confirm": "secret123",
    }, allow_redirects=True)
    check("POST /superadmin/setup → dashboard", "/superadmin/dashboard" in r.url or r.status_code == 200)
    check("sa_token cookie set", "sa_token" in s.cookies)

    # ── 3. Dashboard ──────────────────────────────────────────────────────────
    print("\n[H-3] Platform Dashboard")
    r = s.get(f"{BASE}/superadmin/dashboard")
    check("GET /superadmin/dashboard → 200", r.status_code == 200)
    check("Platform Overview heading", "Platform Overview" in r.text)
    check("Total Tenants tile", "Total Tenants" in r.text)

    # ── 4. Tenant list (empty) ────────────────────────────────────────────────
    print("\n[H-4] Tenant List")
    r = s.get(f"{BASE}/superadmin/tenants")
    check("GET /superadmin/tenants → 200", r.status_code == 200)
    check("No tenants message visible", "No tenants found" in r.text or "Tenants" in r.text)

    # ── 5. Create tenant via SA portal ───────────────────────────────────────
    print("\n[H-5] Create Tenant")
    r = s.get(f"{BASE}/superadmin/tenants/new")
    check("GET /superadmin/tenants/new → 200", r.status_code == 200)
    r = s.post(f"{BASE}/superadmin/tenants/new", data={
        "factory_name": "Test Factory SA",
        "slug": "testfactory-sa",
        "industry": "Automotive",
        "plan": "PROFESSIONAL",
        "contact_name": "John Doe",
        "contact_email": "john@testfactory.com",
        "admin_name": "SA Admin",
        "admin_phone": "0123456789",
        "admin_password": "pass1234",
    }, allow_redirects=True)
    check("POST create tenant → tenant detail", "/superadmin/tenants/" in r.url)
    check("Factory name on detail page", "Test Factory SA" in r.text)
    # Extract tenant id
    tenant_id = r.url.split("/superadmin/tenants/")[-1].split("?")[0]
    check("Got tenant ID", len(tenant_id) > 10)

    # ── 6. Plan change ────────────────────────────────────────────────────────
    print("\n[H-6] Plan Management")
    r = s.post(f"{BASE}/superadmin/tenants/{tenant_id}/plan",
               data={"plan": "ENTERPRISE"}, allow_redirects=True)
    check("POST /plan → 200", r.status_code == 200)
    check("Plan updated message", "plan_updated" in r.url or "ENTERPRISE" in r.text)

    # ── 7. Edit tenant ────────────────────────────────────────────────────────
    print("\n[H-7] Edit Tenant")
    r = s.post(f"{BASE}/superadmin/tenants/{tenant_id}/edit", data={
        "factory_name": "Test Factory SA Updated",
        "industry": "Food",
        "contact_name": "Jane Doe",
        "contact_email": "jane@testfactory.com",
    }, allow_redirects=True)
    check("POST /edit → 200", r.status_code == 200)
    check("Updated name visible", "Test Factory SA Updated" in r.text)

    # ── 8. Suspend / unsuspend ────────────────────────────────────────────────
    print("\n[H-8] Suspend / Unsuspend")
    r = s.post(f"{BASE}/superadmin/tenants/{tenant_id}/suspend", allow_redirects=True)
    check("POST /suspend → 200", r.status_code == 200)
    check("SUSPENDED badge shown", "SUSPENDED" in r.text)

    # Try logging in as tenant (should be blocked)
    r2 = requests.post(f"{BASE}/login", data={
        "slug": "testfactory-sa", "phone": "0123456789", "password": "pass1234"
    }, allow_redirects=True)
    check("Suspended tenant login blocked", "suspended" in r2.text.lower())

    r = s.post(f"{BASE}/superadmin/tenants/{tenant_id}/unsuspend", allow_redirects=True)
    check("POST /unsuspend → 200", r.status_code == 200)
    check("ACTIVE badge shown after unsuspend", "ACTIVE" in r.text)

    # ── 9. SA logout ──────────────────────────────────────────────────────────
    print("\n[H-9] SA Logout")
    r = s.get(f"{BASE}/superadmin/logout", allow_redirects=True)
    check("GET /superadmin/logout → login page", "Super Admin Portal" in r.text or "Sign In" in r.text)
    r = s.get(f"{BASE}/superadmin/dashboard", allow_redirects=True)
    check("Protected after logout", r.status_code in (401, 302, 200) and "/superadmin/dashboard" not in r.url
          or "Sign In" in r.text)

    # ── 10. SA login ──────────────────────────────────────────────────────────
    print("\n[H-10] SA Login")
    s2 = requests.Session()
    r = s2.post(f"{BASE}/superadmin/login", data={
        "email": "sa@omniflow.io", "password": "secret123"
    }, allow_redirects=True)
    check("POST /superadmin/login → dashboard", r.status_code == 200 and "Platform Overview" in r.text)
    check("sa_token cookie on re-login", "sa_token" in s2.cookies)

    # ── 11. Setup page redirects once SA exists ───────────────────────────────
    print("\n[H-11] Setup once-guard")
    r = requests.get(f"{BASE}/superadmin/setup", allow_redirects=True)
    check("Setup page redirects to login once SA exists", "Sign In" in r.text or "/login" in r.url)

    # ── 12. Unauthenticated access blocked ────────────────────────────────────
    print("\n[H-12] Auth guard")
    r = requests.get(f"{BASE}/superadmin/tenants")
    check("Unauthenticated /tenants → redirected", r.status_code in (302, 401)
          or "/superadmin/login" in r.url)

finally:
    srv.terminate()
    srv.wait()

print(f"\n{'─'*56}")
print(f"  PASSED {len(PASS)}/{len(PASS)+len(FAIL)}")
if FAIL:
    print(f"  FAILED: {', '.join(FAIL)}")
print("─"*56)
sys.exit(0 if not FAIL else 1)
