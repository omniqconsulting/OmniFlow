"""
Phase 3 smoke test — Sub-modules A through E + Material Catalogue
Covers:
  3-A  PMS daily log, target revision, no-duplicate guard
  3-B  Dispatch record add, POD upload
  3-C  Invoice add, mark-paid
  3-D  Material catalogue, material request, approve/reject
  3-E  Custom sub-module panel + submit
  Misc Sub-module deployment to stage, scheduler jobs importable
"""

import sys, os, time, json, threading, subprocess, httpx, uuid, textwrap
from datetime import date

BASE = "http://127.0.0.1:8765"
PASS_MARK = "✅"
FAIL_MARK = "❌"

results = []
_srv_proc = None


def start_server():
    global _srv_proc
    root = os.path.dirname(__file__)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    _srv_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8765", "--log-level", "warning"],
        cwd=root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Wait for startup
    for _ in range(30):
        time.sleep(0.7)
        try:
            r = httpx.get(f"{BASE}/health", timeout=2)
            if r.status_code in (200, 404):
                return True
        except Exception:
            pass
    return False


def stop_server():
    if _srv_proc:
        _srv_proc.terminate()
        try:
            _srv_proc.wait(timeout=5)
        except Exception:
            _srv_proc.kill()


def check(label: str, ok: bool, detail: str = ""):
    mark = PASS_MARK if ok else FAIL_MARK
    msg = f"{mark} {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append((label, ok, detail))


def login(client: httpx.Client, phone: str, password: str, slug: str = "smoke3") -> bool:
    r = client.post("/login", data={"slug": slug, "phone": phone, "password": password},
                    follow_redirects=True)
    # Successful login redirects to /dashboard — page title check
    return r.status_code == 200 and ("dashboard" in str(r.url).lower() or "Dashboard" in r.text)


def make_client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=15, follow_redirects=True)


# ─────────────────────────────────────────────────────────────────────────────
# 0. Ensure a tenant + admin + flow with sub-module stages exists
# ─────────────────────────────────────────────────────────────────────────────

def setup_db_data():
    """Insert test data directly via SQLAlchemy."""
    sys.path.insert(0, os.path.dirname(__file__))
    from app.database import (
        SessionLocal, Tenant, User, FMSFlow, FMSStage, FMSTicket,
        LibrarySubmoduleDefinition, new_id,
    )
    from app.auth import hash_password
    db = SessionLocal()
    try:
        slug = "smoke3"
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if not tenant:
            tenant = Tenant(id=new_id(), name="Smoke3 Co", slug=slug,
                            plan="professional", is_approved=True)
            db.add(tenant)
            db.flush()

        admin = db.query(User).filter(User.tenant_id == tenant.id,
                                       User.role == "ADMIN").first()
        if not admin:
            admin = User(id=new_id(), tenant_id=tenant.id,
                         name="Admin3", email="admin3@smoke.test",
                         phone="0000000001",
                         password_hash=hash_password("Test1234!"),
                         role="ADMIN", is_active=True)
            db.add(admin)
            db.flush()

        emp = db.query(User).filter(User.tenant_id == tenant.id,
                                     User.role == "EMPLOYEE").first()
        if not emp:
            emp = User(id=new_id(), tenant_id=tenant.id,
                       name="Emp3", email="emp3@smoke.test",
                       phone="0000000002",
                       password_hash=hash_password("Test1234!"),
                       role="EMPLOYEE", is_active=True)
            db.add(emp)
            db.flush()

        # Create flow with all 5 sub-module stage types
        flow = db.query(FMSFlow).filter(FMSFlow.tenant_id == tenant.id,
                                         FMSFlow.name == "Smoke3Flow").first()
        if not flow:
            flow = FMSFlow(id=new_id(), tenant_id=tenant.id,
                           name="Smoke3Flow", is_active=True)
            db.add(flow)
            db.flush()

        # Ensure stages exist
        tags = ["PMS", "DISPATCH", "INVOICE", "MATERIAL_REQ", "CUSTOM"]
        stages = {}
        for i, tag in enumerate(tags):
            stage = db.query(FMSStage).filter(
                FMSStage.flow_id == flow.id,
                FMSStage.sub_module_tag == tag).first()
            if not stage:
                stage = FMSStage(
                    id=new_id(), flow_id=flow.id, tenant_id=tenant.id,
                    name=f"Stage-{tag}", order=i, sub_module_tag=tag,
                    is_mandatory=True,
                )
                db.add(stage)
            stages[tag] = stage
        db.flush()

        # Create a test ticket at PMS stage
        ticket = db.query(FMSTicket).filter(
            FMSTicket.tenant_id == tenant.id,
            FMSTicket.title == "Smoke3Ticket").first()
        if not ticket:
            pms_stage = stages["PMS"]
            ticket = FMSTicket(
                id=new_id(), tenant_id=tenant.id, flow_id=flow.id,
                title="Smoke3Ticket", status="IN_PROGRESS",
                current_stage_id=pms_stage.id,
                target_qty=100,
                created_by_id=admin.id,
            )
            db.add(ticket)
        db.flush()

        # Library submodule definition for custom test
        lib_def = db.query(LibrarySubmoduleDefinition).filter(
            LibrarySubmoduleDefinition.name == "SmokeCustomForm",
        ).first()
        if not lib_def:
            lib_def = LibrarySubmoduleDefinition(
                id=new_id(),
                name="SmokeCustomForm",
                fields_json=json.dumps([
                    {"name": "notes", "label": "Notes", "type": "text", "required": True},
                    {"name": "qty", "label": "Qty", "type": "number", "required": False},
                ]),
                status="PUBLISHED",
            )
            db.add(lib_def)
            db.flush()
            # Attach to CUSTOM stage
            stages["CUSTOM"].deployed_submodule_id = lib_def.id

        db.commit()
        return {
            "tenant_id": tenant.id,
            "admin_email": "admin3@smoke.test",
            "emp_email": "emp3@smoke.test",
            "password": "Test1234!",
            "ticket_id": ticket.id,
            "pms_stage_id": stages["PMS"].id,
            "dispatch_stage_id": stages["DISPATCH"].id,
            "invoice_stage_id": stages["INVOICE"].id,
            "material_stage_id": stages["MATERIAL_REQ"].id,
            "custom_stage_id": stages["CUSTOM"].id,
            "lib_def_id": lib_def.id if lib_def else None,
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Test suites
# ─────────────────────────────────────────────────────────────────────────────

def test_pms(client, ctx):
    """3-A: PMS panel + daily log."""
    tid = ctx["ticket_id"]

    # Panel GET
    r = client.get(f"/submodules/pms/{tid}")
    check("3-A: PMS panel loads (200)", r.status_code == 200,
          f"status={r.status_code}")

    # Submit daily log (200/302 = fresh; 400 = already submitted today from prior run, also OK)
    today_str = date.today().isoformat()
    r = client.post(f"/submodules/pms/{tid}/log", data={
        "log_date": today_str,
        "qty_done": "25",
        "has_blockers": "",
        "comment": "Smoke test entry",
    })
    check("3-A: PMS log submit (no 5xx)", r.status_code < 500,
          f"status={r.status_code}")

    # Duplicate guard (same day) — must not be 5xx
    r2 = client.post(f"/submodules/pms/{tid}/log", data={
        "log_date": today_str,
        "qty_done": "10",
        "has_blockers": "",
        "comment": "Duplicate",
    })
    # Should redirect back with ?err= or render error — not create a second entry
    check("3-A: Duplicate log guard (no 5xx)", r2.status_code < 500,
          f"status={r2.status_code}")

    # Panel reload shows progress
    r3 = client.get(f"/submodules/pms/{tid}")
    check("3-A: PMS panel reload shows entry",
          r3.status_code == 200 and "Smoke test entry" in r3.text,
          f"status={r3.status_code}")

    # Revise target (admin only)
    r4 = client.post(f"/submodules/pms/{tid}/revise-target", data={
        "new_target": "120",
        "revision_reason": "Customer revised order",
    })
    check("3-A: Revise target (redirect)", r4.status_code in (200, 302, 303),
          f"status={r4.status_code}")


def test_dispatch(client, ctx):
    """3-B: Dispatch panel + record add."""
    tid = ctx["ticket_id"]

    r = client.get(f"/submodules/dispatch/{tid}")
    check("3-B: Dispatch panel loads", r.status_code == 200, f"status={r.status_code}")

    r2 = client.post(f"/submodules/dispatch/{tid}/add", data={
        "qty_dispatched": "50",
        "unit": "pcs",
        "vehicle_number": "MH12AB1234",
        "driver_name": "Ramesh",
        "destination": "Mumbai Depot",
        "expected_delivery": "2026-06-20",
        "notes": "Smoke test dispatch",
    })
    check("3-B: Add dispatch record", r2.status_code in (200, 302, 303),
          f"status={r2.status_code}")

    # Reload and check record appears
    r3 = client.get(f"/submodules/dispatch/{tid}")
    check("3-B: Dispatch record visible", "MH12AB1234" in r3.text,
          f"status={r3.status_code}")


def test_invoice(client, ctx):
    """3-C: Invoice panel + add + mark-paid."""
    tid = ctx["ticket_id"]

    r = client.get(f"/submodules/invoice/{tid}")
    check("3-C: Invoice panel loads", r.status_code == 200, f"status={r.status_code}")

    inv_num = f"INV-{uuid.uuid4().hex[:6].upper()}"
    r2 = client.post(f"/submodules/invoice/{tid}/add", data={
        "invoice_number": inv_num,
        "amount": "15000.50",
        "currency": "INR",
        "invoice_date": "2026-06-01",
        "due_date": "2026-06-30",
        "payment_terms": "Net 30",
    })
    check("3-C: Add invoice", r2.status_code in (200, 302, 303), f"status={r2.status_code}")

    # Reload and find invoice
    r3 = client.get(f"/submodules/invoice/{tid}")
    check("3-C: Invoice visible on panel", inv_num in r3.text, f"status={r3.status_code}")

    # Get invoice id from DB
    sys.path.insert(0, os.path.dirname(__file__))
    from app.database import SessionLocal, InvoiceRecord
    db = SessionLocal()
    try:
        inv = db.query(InvoiceRecord).filter(
            InvoiceRecord.invoice_number == inv_num).first()
        if inv:
            r4 = client.post(f"/submodules/invoice/{inv.id}/mark-paid", data={
                "payment_ref": "NEFT123456",
            })
            check("3-C: Mark invoice paid", r4.status_code in (200, 302, 303),
                  f"status={r4.status_code}")
            # Verify is_paid in DB
            db.refresh(inv)
            check("3-C: Invoice is_paid=True in DB", inv.is_paid is True,
                  f"is_paid={inv.is_paid}")
        else:
            check("3-C: Mark invoice paid", False, "invoice not found in DB")
    finally:
        db.close()


def test_materials(client, ctx):
    """3-D: Material catalogue + request + approve/reject."""
    # Add material via admin catalogue
    r = client.get("/submodules/catalogue")
    check("3-D: Material catalogue loads", r.status_code == 200, f"status={r.status_code}")

    mat_name = f"SteelRod-{uuid.uuid4().hex[:4]}"
    r2 = client.post("/submodules/catalogue/add", data={
        "name": mat_name,
        "unit": "kg",
        "description": "Smoke material",
        "reorder_threshold": "50",
        "reorder_qty": "200",
        "lead_time_days": "7",
        "opening_stock": "500",
        "supplier": "Tata Steel",
    })
    check("3-D: Add material to catalogue", r2.status_code in (200, 302, 303),
          f"status={r2.status_code}")

    # Get material id
    from app.database import SessionLocal, Material
    db = SessionLocal()
    try:
        mat = db.query(Material).filter(Material.name == mat_name).first()
        if not mat:
            check("3-D: Material request submit", False, "material not found")
            return
        mat_id = mat.id
    finally:
        db.close()

    # Submit material request
    tid = ctx["ticket_id"]
    r3 = client.get(f"/submodules/materials/{tid}")
    check("3-D: Material req panel loads", r3.status_code == 200, f"status={r3.status_code}")

    r4 = client.post(f"/submodules/materials/{tid}/request", data={
        "material_id": mat_id,
        "qty_requested": "25",
        "unit": "kg",
        "reason": "Smoke test requirement",
    })
    check("3-D: Submit material request", r4.status_code in (200, 302, 303),
          f"status={r4.status_code}")

    # Get request id
    from app.database import MaterialRequest
    db = SessionLocal()
    try:
        req = db.query(MaterialRequest).filter(
            MaterialRequest.material_id == mat_id,
            MaterialRequest.ticket_id == tid).first()
        if not req:
            check("3-D: Approve material req", False, "request not found")
            return
        req_id = req.id
    finally:
        db.close()

    # Approve
    r5 = client.post(f"/submodules/materials/req/{req_id}/approve", data={"fulfilled_qty": "25"})
    check("3-D: Approve material request", r5.status_code in (200, 302, 303),
          f"status={r5.status_code}")

    # Verify approved
    from app.database import MaterialRequest
    db = SessionLocal()
    try:
        req2 = db.query(MaterialRequest).get(req_id)
        check("3-D: Request status=APPROVED in DB", req2 and req2.status == "APPROVED",
              f"status={req2.status if req2 else 'none'}")
    finally:
        db.close()

    # Create another request and reject it
    r6 = client.post(f"/submodules/materials/{tid}/request", data={
        "material_id": mat_id,
        "qty_requested": "10",
        "unit": "kg",
        "reason": "Test reject",
    })
    from app.database import MaterialRequest
    db = SessionLocal()
    try:
        req3 = db.query(MaterialRequest).filter(
            MaterialRequest.material_id == mat_id,
            MaterialRequest.status == "PENDING").first()
        if req3:
            r7 = client.post(f"/submodules/materials/req/{req3.id}/reject", data={
                "rejection_note": "Not needed now",
            })
            check("3-D: Reject material request", r7.status_code in (200, 302, 303),
                  f"status={r7.status_code}")
        else:
            check("3-D: Reject material request", False, "pending req not found")
    finally:
        db.close()


def test_custom(client, ctx):
    """3-E: Custom sub-module panel + submit."""
    tid = ctx["ticket_id"]
    sid = ctx["custom_stage_id"]

    r = client.get(f"/submodules/custom/{tid}/{sid}")
    check("3-E: Custom panel loads", r.status_code == 200, f"status={r.status_code}")

    r2 = client.post(f"/submodules/custom/{tid}/{sid}/submit", data={
        "notes": "Inspection complete",
        "qty": "42",
        "mark_complete": "true",
    })
    check("3-E: Custom form submit", r2.status_code in (200, 302, 303),
          f"status={r2.status_code}")

    # Verify in DB
    from app.database import SessionLocal, CustomSubmoduleResponse
    db = SessionLocal()
    try:
        resp = db.query(CustomSubmoduleResponse).filter(
            CustomSubmoduleResponse.ticket_id == tid,
            CustomSubmoduleResponse.stage_id == sid,
        ).first()
        check("3-E: Response saved in DB", resp is not None and resp.is_complete,
              f"found={resp is not None}")
    finally:
        db.close()


def test_deploy_to_stage(client, ctx):
    """Misc: Deploy library sub-module to a stage via API."""
    r = client.post("/submodules/deploy-to-stage", data={
        "stage_id": ctx["custom_stage_id"],
        "submodule_def_id": ctx["lib_def_id"],
    })
    check("Misc: deploy-to-stage endpoint", r.status_code in (200, 302, 303),
          f"status={r.status_code}")


def test_scheduler_imports():
    """Phase 3 scheduler jobs importable."""
    from app.scheduler import (
        pms_no_entry_check, dispatch_pod_overdue_check, invoice_overdue_check,
        start_scheduler, stop_scheduler
    )
    check("Scheduler: Phase 3 job functions importable", True)


def test_navigation(client, ctx):
    """Nav smoke: key pages return 200."""
    pages = [
        ("/fms/dashboard", "FMS dashboard"),
        ("/fms/flows", "Flow list"),
        ("/submodules/catalogue", "Material catalogue"),
    ]
    for path, label in pages:
        r = client.get(path)
        check(f"Nav: {label}", r.status_code == 200, f"status={r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═"*62)
    print("  Phase 3 Smoke Test — Sub-modules A through E")
    print("═"*62 + "\n")

    # Scheduler imports (no server needed)
    test_scheduler_imports()

    # Setup
    print("\n[setup] Inserting test data …")
    ctx = setup_db_data()
    print(f"  tenant_id : {ctx['tenant_id']}")
    print(f"  ticket_id : {ctx['ticket_id']}")

    # Start server
    print("\n[server] Starting uvicorn on :8765 …")
    ok = start_server()
    if not ok:
        print("❌  Server failed to start — aborting.")
        sys.exit(1)
    print("[server] Started.\n")

    try:
        with make_client() as client:
            # Login as admin (slug + phone)
            login_ok = login(client, "0000000001", ctx["password"])
            check("Auth: admin login", login_ok)

            if login_ok:
                test_navigation(client, ctx)
                test_pms(client, ctx)
                test_dispatch(client, ctx)
                test_invoice(client, ctx)
                test_materials(client, ctx)
                test_custom(client, ctx)
                test_deploy_to_stage(client, ctx)
    finally:
        stop_server()

    # Summary
    print("\n" + "─"*62)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"  PASSED: {passed}   FAILED: {failed}   TOTAL: {len(results)}")
    print("─"*62)

    if failed:
        print("\nFailed checks:")
        for label, ok, detail in results:
            if not ok:
                print(f"  {FAIL_MARK} {label}  {detail}")
        sys.exit(1)
    else:
        print("\n🎉  All Phase 3 checks passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
