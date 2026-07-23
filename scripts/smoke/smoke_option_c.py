"""
Option C Self-Registration + SA Approval Smoke Test
"""
import sys, os, requests, time, subprocess

BASE = "http://localhost:8000"
PASS = []
FAIL = []

def check(label, cond, detail=""):
    if cond:
        print(f"  OK  {label}")
        PASS.append(label)
    else:
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))
        FAIL.append(label)

proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
py   = os.path.join(proj, "venv", "Scripts", "python.exe")
srv  = subprocess.Popen(
    [py, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
    cwd=proj, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(3)

try:
    print("\n== Option C: Self-Registration + SA Approval ==\n")

    # 1. Self-register a factory
    print("[1] Client self-registers")
    r = requests.post(f"{BASE}/register", data={
        "factory_name": "Sunrise Textiles",
        "slug": "sunrise-textiles",
        "name": "Raj Kumar",
        "phone": "0111222333",
        "password": "pass1234",
        "contact_email": "raj@sunrise.com",
    }, allow_redirects=True)
    check("POST /register -> pending page", r.status_code == 200)
    check("Pending approval message shown", "Pending Approval" in r.text or "pending" in r.text.lower())
    check("Factory name shown on confirmation", "Sunrise Textiles" in r.text)
    check("NOT redirected to dashboard", "Platform Overview" not in r.text and "Open Tickets" not in r.text)

    # 2. Client tries to login - should work but land on pending page
    print("\n[2] Client logs in - lands on pending page")
    s_client = requests.Session()
    r = s_client.post(f"{BASE}/login", data={
        "slug": "sunrise-textiles", "phone": "0111222333", "password": "pass1234"
    }, allow_redirects=True)
    check("Login succeeds (not blocked)", r.status_code == 200)
    check("Lands on pending approval page", "under review" in r.text or "Pending" in r.text)
    check("Dashboard content NOT shown", "Open Tickets" not in r.text)

    # 3. Setup SA and login
    print("\n[3] Super Admin setup + login")
    s_sa = requests.Session()
    r = s_sa.post(f"{BASE}/superadmin/setup", data={
        "name": "Platform Admin", "email": "sa@omniflow.io",
        "password": "sapass123", "confirm": "sapass123",
    }, allow_redirects=True)
    check("SA setup completes", r.status_code == 200 and "Platform Overview" in r.text)

    # 4. SA dashboard shows pending alert
    print("\n[4] SA sees pending registration")
    r = s_sa.get(f"{BASE}/superadmin/dashboard")
    check("Pending alert on SA dashboard", "waiting for approval" in r.text or "Pending" in r.text)
    check("Factory name in pending list", "Sunrise Textiles" in r.text)

    # 5. SA approvals page
    print("\n[5] SA approvals queue")
    r = s_sa.get(f"{BASE}/superadmin/approvals")
    check("GET /superadmin/approvals -> 200", r.status_code == 200)
    check("Sunrise Textiles in queue", "Sunrise Textiles" in r.text)
    check("TRIAL badge shown", "TRIAL" in r.text)
    check("Approve button present", "Approve" in r.text)

    # 6. Extract tenant ID from tenant list
    r = s_sa.get(f"{BASE}/superadmin/tenants")
    import re
    tid_match = re.search(r'/superadmin/tenants/([a-f0-9\-]{36})', r.text)
    tid = tid_match.group(1) if tid_match else None
    check("Tenant ID found", tid is not None)

    # 7. Approve the tenant
    print("\n[6] SA approves the registration")
    r = s_sa.post(f"{BASE}/superadmin/tenants/{tid}/approve",
                  data={"plan": "STARTER"}, allow_redirects=True)
    check("POST /approve -> 200", r.status_code == 200)
    check("Approval success message", "approved" in r.url or "approved" in r.text.lower())

    # 8. Client now logs in and sees dashboard
    print("\n[7] Client logs in after approval")
    s_client2 = requests.Session()
    r = s_client2.post(f"{BASE}/login", data={
        "slug": "sunrise-textiles", "phone": "0111222333", "password": "pass1234"
    }, allow_redirects=True)
    check("Login post-approval -> dashboard", r.status_code == 200)
    check("Full dashboard visible", "Open Tickets" in r.text or "Dashboard" in r.text)
    check("Pending page NOT shown", "under review" not in r.text)

    # 9. Reject flow — register another factory
    print("\n[8] SA rejects a registration")
    requests.post(f"{BASE}/register", data={
        "factory_name": "Spam Factory",
        "slug": "spam-factory",
        "name": "Bot User",
        "phone": "0999999999",
        "password": "pass1234",
    })
    r = s_sa.get(f"{BASE}/superadmin/tenants")
    tid2_match = re.search(r'/superadmin/tenants/([a-f0-9\-]{36})', r.text)
    # Find the second (newest) tenant
    all_tids = re.findall(r'/superadmin/tenants/([a-f0-9\-]{36})', r.text)
    tid2 = next((t for t in all_tids if t != tid), None)
    if tid2:
        r = s_sa.post(f"{BASE}/superadmin/tenants/{tid2}/reject",
                      data={"reason": "spam"}, allow_redirects=True)
        check("POST /reject -> 200", r.status_code == 200)
        check("Rejected message shown", "rejected" in r.url or "rejected" in r.text.lower())
        # Rejected tenant can't login
        r = requests.post(f"{BASE}/login", data={
            "slug": "spam-factory", "phone": "0999999999", "password": "pass1234"
        }, allow_redirects=True)
        check("Rejected tenant login blocked", "suspended" in r.text.lower() or r.status_code == 200 and "under review" not in r.text)
    else:
        check("Second tenant found for reject test", False)

    # 10. SA can still create tenant directly (already-approved)
    print("\n[9] SA-created tenant bypasses approval")
    r = s_sa.post(f"{BASE}/superadmin/tenants/new", data={
        "factory_name": "SA Direct Factory",
        "slug": "sa-direct",
        "plan": "PROFESSIONAL",
        "contact_name": "Direct Admin",
        "contact_email": "",
        "admin_name": "Direct Admin",
        "admin_phone": "0888888888",
        "admin_password": "pass1234",
    }, allow_redirects=True)
    check("SA creates tenant -> detail page", "/superadmin/tenants/" in r.url)
    # SA-created tenants should be is_approved=True by default
    sa_tid = r.url.split("/superadmin/tenants/")[-1].split("?")[0]
    r2 = requests.post(f"{BASE}/login", data={
        "slug": "sa-direct", "phone": "0888888888", "password": "pass1234"
    }, allow_redirects=True)
    check("SA-created tenant logs in to dashboard", "Open Tickets" in r2.text or "Dashboard" in r2.text)

finally:
    srv.terminate()
    srv.wait()

print(f"\n{'='*50}")
print(f"  PASSED {len(PASS)}/{len(PASS)+len(FAIL)}")
if FAIL:
    print(f"  FAILED: {', '.join(FAIL)}")
print('='*50)
sys.exit(0 if not FAIL else 1)
