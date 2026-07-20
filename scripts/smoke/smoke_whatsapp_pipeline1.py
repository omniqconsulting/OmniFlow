"""
smoke_whatsapp_pipeline1.py
Automated checks for WhatsApp Pipeline 1 — Ticket Assigned.
Follows the existing smoke_phaseN.py convention: spins up uvicorn, drives it
with requests, simple OK/FAIL check() helper.

Real WhatsApp delivery (Scenario 2) requires a manual check on the test phone
after the script completes — annotated below.

Run:
    python smoke_whatsapp_pipeline1.py
"""
import subprocess, sys, time, requests, os, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

BASE = "http://127.0.0.1:8765"
PASS = "\033[32mOK\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []


def check(label, ok, detail=""):
    tag = PASS if ok else FAIL
    print(f"  [{tag}] {label}" + (f" — {detail}" if detail else ""))
    results.append((label, ok))


def login(session, email, password):
    r = session.post(f"{BASE}/login", data={"email": email, "password": password},
                     allow_redirects=False)
    return r.status_code in (200, 302, 303)


# ── Boot server ───────────────────────────────────────────────────────────────
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8765", "--log-level", "warning"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(4)

try:
    # ── Auth ──────────────────────────────────────────────────────────────────
    s = requests.Session()
    admin_email = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@test.com")
    admin_pw    = os.environ.get("SMOKE_ADMIN_PW",    "password")
    logged_in   = login(s, admin_email, admin_pw)
    check("Admin login", logged_in)
    if not logged_in:
        print("\nCannot continue without admin session.")
        sys.exit(1)

    # Discover a test employee for toggle tests
    from app.database import SessionLocal, User
    db = SessionLocal()
    emp = db.query(User).filter(User.role == "EMPLOYEE", User.is_deleted == False).first()
    if not emp:
        print("No employee found — seed the DB first.")
        sys.exit(1)
    emp_id = emp.id
    emp_name = emp.name

    # ── Scenario 1: Mark as Validated ────────────────────────────────────────
    r = s.post(f"{BASE}/employees/{emp_id}/toggle-validated", allow_redirects=False)
    check("Scenario 1 — toggle-validated returns redirect", r.status_code == 303)
    db.expire_all()
    emp_fresh = db.query(User).filter(User.id == emp_id).first()
    if emp_fresh.mobile_verified:
        check("Scenario 1 — mobile_verified=True", True)
        check("Scenario 1 — mobile_verified_at set", emp_fresh.mobile_verified_at is not None)
        check("Scenario 1 — mobile_verified_by set", emp_fresh.mobile_verified_by is not None)
        validated_state = True
    else:
        # Was already validated — unmark happened; toggle back
        r2 = s.post(f"{BASE}/employees/{emp_id}/toggle-validated", allow_redirects=False)
        db.expire_all()
        emp_fresh = db.query(User).filter(User.id == emp_id).first()
        check("Scenario 1 — mobile_verified=True (re-toggled)", emp_fresh.mobile_verified)
        check("Scenario 1 — mobile_verified_at set", emp_fresh.mobile_verified_at is not None)
        check("Scenario 1 — mobile_verified_by set", emp_fresh.mobile_verified_by is not None)
        validated_state = emp_fresh.mobile_verified

    # ── Scenario 2: Ticket assigned to VALIDATED employee ────────────────────
    # Ensure employee is validated before creating the ticket.
    if not emp_fresh.mobile_verified:
        s.post(f"{BASE}/employees/{emp_id}/toggle-validated", allow_redirects=False)
        db.expire_all()

    from app.database import Ticket, WhatsAppMessageLog
    ticket_data = {
        "title": "Smoke Test Ticket (validated)",
        "description": "WhatsApp pipeline smoke test",
        "priority": "HIGH",
        "assignee_id": emp_id,
        "due_at": "2026-12-31",
    }
    r = s.post(f"{BASE}/tickets/create", data=ticket_data, allow_redirects=False)
    check("Scenario 2 — ticket creation returns redirect", r.status_code in (302, 303))
    import time as _t; _t.sleep(1)
    ticket = db.query(Ticket).filter(
        Ticket.current_assignee_id == emp_id,
        Ticket.title == "Smoke Test Ticket (validated)",
    ).order_by(Ticket.created_at.desc()).first()
    check("Scenario 2 — ticket created", ticket is not None)
    if ticket:
        log = db.query(WhatsAppMessageLog).filter(
            WhatsAppMessageLog.related_entity_id == ticket.id
        ).first()
        check("Scenario 2 — whatsapp_message_log row created", log is not None)
        if log:
            check("Scenario 2 — status is SENT or FAILED (not SKIPPED)",
                  log.status in ("SENT", "FAILED"),
                  f"status={log.status}")
            print(f"\n  [MANUAL] Scenario 2 — check test phone {emp_fresh.phone} for WhatsApp message.")
            print(f"           Log status: {log.status}"
                  + (f" | error: {log.error_message}" if log.error_message else ""))

    # ── Scenario 3: Ticket assigned to NOT-VALIDATED employee ─────────────────
    # Unmark the employee first.
    s.post(f"{BASE}/employees/{emp_id}/toggle-validated", allow_redirects=False)
    db.expire_all()
    emp_unvalidated = db.query(User).filter(User.id == emp_id).first()
    if emp_unvalidated.mobile_verified:
        # still validated — toggle once more
        s.post(f"{BASE}/employees/{emp_id}/toggle-validated", allow_redirects=False)
        db.expire_all()
        emp_unvalidated = db.query(User).filter(User.id == emp_id).first()
    check("Scenario 3 — employee is NOT validated", not emp_unvalidated.mobile_verified)

    ticket_data2 = {
        "title": "Smoke Test Ticket (not validated)",
        "description": "WhatsApp pipeline smoke test",
        "priority": "LOW",
        "assignee_id": emp_id,
        "due_at": "2026-12-31",
    }
    r = s.post(f"{BASE}/tickets/create", data=ticket_data2, allow_redirects=False)
    check("Scenario 3 — ticket creation succeeds", r.status_code in (302, 303))
    _t.sleep(1)
    ticket3 = db.query(Ticket).filter(
        Ticket.current_assignee_id == emp_id,
        Ticket.title == "Smoke Test Ticket (not validated)",
    ).order_by(Ticket.created_at.desc()).first()
    check("Scenario 3 — ticket created (assignment succeeded)", ticket3 is not None)
    if ticket3:
        log3 = db.query(WhatsAppMessageLog).filter(
            WhatsAppMessageLog.related_entity_id == ticket3.id
        ).first()
        check("Scenario 3 — log row exists", log3 is not None)
        if log3:
            check("Scenario 3 — status=SKIPPED_UNVERIFIED", log3.status == "SKIPPED_UNVERIFIED",
                  f"got {log3.status}")

    # ── Scenario 4: Resend on FAILED row ─────────────────────────────────────
    # Insert a synthetic FAILED log row, then resend it.
    from app.database import new_id
    from datetime import datetime
    fake_log = WhatsAppMessageLog(
        id=new_id(),
        tenant_id=emp_fresh.tenant_id,
        template_name="omniflow_ticket_assigned",
        recipient_user_id=emp_id,
        recipient_phone=emp_fresh.phone,
        variables_json=json.dumps([emp_fresh.name, "Smoke Test", "HIGH", "31st Dec 2026"]),
        status="FAILED",
        error_message="Simulated failure for smoke test",
        related_entity_type="ticket",
        related_entity_id=ticket.id if ticket else "smoke",
        attempt_count=1,
    )
    db.add(fake_log)
    db.commit()
    fake_log_id = fake_log.id

    r = s.post(f"{BASE}/whatsapp-log/{fake_log_id}/resend",
               headers={"referer": f"{BASE}/tickets/{ticket.id if ticket else ''}"},
               allow_redirects=False)
    check("Scenario 4 — resend returns redirect", r.status_code == 303)
    db.expire_all()
    refreshed = db.query(WhatsAppMessageLog).filter(WhatsAppMessageLog.id == fake_log_id).first()
    check("Scenario 4 — attempt_count incremented", refreshed.attempt_count == 2,
          f"attempt_count={refreshed.attempt_count}")
    check("Scenario 4 — last_attempted_at updated", refreshed.last_attempted_at is not None)
    check("Scenario 4 — status updated (SENT or FAILED)", refreshed.status in ("SENT", "FAILED"),
          f"status={refreshed.status}")

    # ── Scenario 5: Resend blocked on non-FAILED row ──────────────────────────
    if log3:
        r = s.post(f"{BASE}/whatsapp-log/{log3.id}/resend", allow_redirects=False)
        check("Scenario 5 — resend on SKIPPED_UNVERIFIED returns 400",
              r.status_code == 400, f"got {r.status_code}")

    # ── Scenario 6: Un-mark employee ─────────────────────────────────────────
    # Re-validate first, then unmark.
    s.post(f"{BASE}/employees/{emp_id}/toggle-validated", allow_redirects=False)
    db.expire_all()
    emp_revalidated = db.query(User).filter(User.id == emp_id).first()
    if not emp_revalidated.mobile_verified:
        s.post(f"{BASE}/employees/{emp_id}/toggle-validated", allow_redirects=False)
        db.expire_all()
        emp_revalidated = db.query(User).filter(User.id == emp_id).first()
    check("Scenario 6 — re-validated", emp_revalidated.mobile_verified)

    s.post(f"{BASE}/employees/{emp_id}/toggle-validated", allow_redirects=False)
    db.expire_all()
    emp_unmarked = db.query(User).filter(User.id == emp_id).first()
    check("Scenario 6 — mobile_verified cleared", not emp_unmarked.mobile_verified)
    check("Scenario 6 — mobile_verified_at cleared", emp_unmarked.mobile_verified_at is None)
    check("Scenario 6 — mobile_verified_by cleared", emp_unmarked.mobile_verified_by is None)

    # ── Scenario 7: Ticket detail page renders without error ──────────────────
    if ticket:
        r = s.get(f"{BASE}/tickets/{ticket.id}")
        check("Scenario 7 — ticket detail page loads", r.status_code == 200)
        check("Scenario 7 — WhatsApp in timeline", "WhatsApp" in r.text)

    db.close()

finally:
    proc.terminate()

# ── Summary ───────────────────────────────────────────────────────────────────
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"\n{'='*50}")
print(f"WhatsApp Pipeline 1 Smoke Test: {passed}/{total} passed")
if passed < total:
    print("FAILED scenarios:")
    for label, ok in results:
        if not ok:
            print(f"  - {label}")
    sys.exit(1)
else:
    print("All checks passed.")
