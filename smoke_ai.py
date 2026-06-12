"""
AI Feature smoke test — access control, page render, context endpoint
"""
import requests, sys
BASE = "http://127.0.0.1:8000"
OK = "[PASS]"; FAIL = "[FAIL]"
passed = failed = 0

def test(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  {OK}  {name}"); passed += 1
    else:
        print(f"  {FAIL}  {name}  {detail}"); failed += 1

print("\n=== AI Feature Smoke Test ===\n")

# ── Setup users ───────────────────────────────────────────────────────────────
from app.database import SessionLocal, User, Tenant
from app.auth import hash_password

db = SessionLocal()
t = db.query(Tenant).filter(Tenant.slug == "invco2").first()
test("Tenant exists", bool(t))

for phone, email, role in [
    ("9001000001", "admin@invco2.com",   "ADMIN"),
    ("9001000099", "mgr@invco2.com",     "MANAGER"),
    ("9001000098", "emp_ai@invco2.com",  "EMPLOYEE"),
]:
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(tenant_id=t.id, name=role.title(), phone=phone, email=email,
                 role=role, password_hash=hash_password("pw123"), is_active=True)
        db.add(u)
db.commit()
db.close()
test("Test users ready", True)

# ── Admin access ──────────────────────────────────────────────────────────────
print("\n--- Admin access ---")
ta = requests.Session()
ta.post(f"{BASE}/login", data={"slug": "invco2", "phone": "9001000001", "password": "pw123"})

r = ta.get(f"{BASE}/ai")
test("Admin: /ai page loads (200)", r.status_code == 200, f"got {r.status_code}")
test("Admin: page contains ask-btn", "ask-btn" in r.text)
test("Admin: page contains hero section", "ai-hero" in r.text)
test("Admin: shows api-warning (no key set)", "api-warning" in r.text)
test("Admin: example prompt chips present", "prompt-chip" in r.text)

# AI pill in main nav (check dashboard page)
r = ta.get(f"{BASE}/dashboard")
test("Admin: AI pill visible in main nav", "ai-pill" in r.text)
test("Admin: AI pill links to /ai", 'href="/ai"' in r.text)

# ── Manager access ────────────────────────────────────────────────────────────
print("\n--- Manager access ---")
tm = requests.Session()
tm.post(f"{BASE}/login", data={"slug": "invco2", "phone": "9001000099", "password": "pw123"})

r = tm.get(f"{BASE}/ai")
test("Manager: /ai page loads (200)", r.status_code == 200, f"got {r.status_code}")
test("Manager: AI pill visible in nav", "ai-pill" in r.text)

# ── Employee blocked ──────────────────────────────────────────────────────────
print("\n--- Employee blocked ---")
te = requests.Session()
te.post(f"{BASE}/login", data={"slug": "invco2", "phone": "9001000098", "password": "pw123"})

r = te.get(f"{BASE}/ai", allow_redirects=False)
test("Employee: /ai returns 403", r.status_code == 403, f"got {r.status_code}")

# Employee nav should NOT have the AI pill
r = te.get(f"{BASE}/dashboard", allow_redirects=True)
test("Employee: AI pill NOT in their nav", 'href="/ai"' not in r.text)

# ── Context endpoint ──────────────────────────────────────────────────────────
print("\n--- Context endpoint ---")
r = ta.get(f"{BASE}/ai/context")
test("Admin: /ai/context returns 200", r.status_code == 200, f"got {r.status_code}")
data = r.json()
test("Context has 'context' key", "context" in data)
test("Context has 'generated_at' key", "generated_at" in data)
ctx = data.get("context", "")
test("Context contains Organisation section", "Organisation" in ctx)
test("Context contains Tickets section", "Tickets" in ctx or "Snapshot" in ctx)
test("Context contains Team/Employee section", "Performance" in ctx or "Team" in ctx)
preview = ctx[:300].encode('ascii', 'replace').decode('ascii').replace('\n', ' | ')
print(f"\n  Context preview (first 300 chars):\n  {preview}")

# Manager cannot access context endpoint (admin only)
r = tm.get(f"{BASE}/ai/context", allow_redirects=False)
test("Manager: /ai/context returns 403", r.status_code == 403, f"got {r.status_code}")

# ── ASK endpoint (no API key → graceful 503) ──────────────────────────────────
print("\n--- Ask endpoint (no key configured) ---")
r = ta.post(f"{BASE}/ai/ask", data={"question": "How many open tickets are there?"})
test("Ask without API key returns 503", r.status_code == 503, f"got {r.status_code}")

# Empty question
import os; os.environ["ANTHROPIC_API_KEY"] = "test"  # set fake key so 503 becomes passthrough
r = ta.post(f"{BASE}/ai/ask", data={"question": ""})
test("Ask with empty question returns 400", r.status_code == 400, f"got {r.status_code}")

# Question too long
r = ta.post(f"{BASE}/ai/ask", data={"question": "x" * 1001})
test("Ask with 1001-char question returns 400", r.status_code == 400, f"got {r.status_code}")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*40}")
print(f"  Passed: {passed}  /  Total: {passed + failed}")
if failed:
    print(f"  FAILED: {failed}")
    sys.exit(1)
else:
    print("  ALL TESTS PASSED")
