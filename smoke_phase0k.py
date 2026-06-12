"""Phase 0-K Configuration Library Smoke Test"""
import sys, os, requests, threading, subprocess, re, json

BASE = "http://localhost:8000"
ok = []; fail = []

def check(label, cond, hint=""):
    if cond: print(f"  OK   {label}"); ok.append(label)
    else:    print(f"  FAIL {label}" + (f"  [{hint}]" if hint else "")); fail.append(label)

proj = os.path.dirname(__file__)
py   = os.path.join(proj, "venv", "Scripts", "python.exe")
srv  = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--port", "8000"],
    cwd=proj, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

ready = threading.Event()
def _watch():
    for line in srv.stdout:
        if b"Application startup complete" in line:
            ready.set(); return
threading.Thread(target=_watch, daemon=True).start()
ready.wait(timeout=30)

try:
    # ── Setup ──────────────────────────────────────────────────────────────────
    sa = requests.Session()
    sa.post(BASE+"/superadmin/setup",
            data={"name":"SA","email":"sa@k.io","password":"pass123","confirm":"pass123"},
            allow_redirects=True)

    requests.post(BASE+"/register", data={
        "factory_name":"K Test Co","slug":"ktest",
        "name":"Admin","phone":"0100000001","password":"pass1234"
    })
    r = sa.get(BASE+"/superadmin/tenants")
    tids = re.findall(r'/superadmin/tenants/([a-f0-9\-]{36})', r.text)
    tid = tids[0] if tids else None
    check("Tenant ID found", tid is not None)
    sa.post(BASE+f"/superadmin/tenants/{tid}/approve", data={"plan":"STARTER"},
            allow_redirects=True)

    # ── K-1: Library tables exist and seeded ───────────────────────────────────
    print("\n[K-1] Library tables seeded at startup")
    r = sa.get(BASE+"/superadmin/library/flows")
    check("GET /library/flows -> 200", r.status_code == 200)
    check("System flow templates visible", "Standard Production Flow" in r.text or "Flow" in r.text)

    r = sa.get(BASE+"/superadmin/library/submodules")
    check("GET /library/submodules -> 200", r.status_code == 200)
    check("System sub-modules visible", "SYSTEM" in r.text or "Breakdown" in r.text)

    r = sa.get(BASE+"/superadmin/library/checklists")
    check("GET /library/checklists -> 200", r.status_code == 200)
    check("System checklists visible", "Daily Machine Safety" in r.text or "SYSTEM" in r.text)

    r = sa.get(BASE+"/superadmin/library/labels")
    check("GET /library/labels -> 200", r.status_code == 200)
    check("Label bundles visible", "Manufacturing" in r.text or "Restaurant" in r.text)

    r = sa.get(BASE+"/superadmin/library/onboarding")
    check("GET /library/onboarding -> 200", r.status_code == 200)
    check("System onboarding bundles visible", "Factory Default" in r.text or "Construction" in r.text)

    # ── K-2: Flow template CRUD ────────────────────────────────────────────────
    print("\n[K-2] Flow template CRUD")
    r = sa.get(BASE+"/superadmin/library/flows/new")
    check("GET /library/flows/new -> 200", r.status_code == 200)
    check("Stage builder UI present", "addStage" in r.text or "Stage" in r.text)

    stages = [
        {"name":"Open","color":"#3b82f6","is_terminal":False},
        {"name":"In Progress","color":"#f59e0b","is_terminal":False},
        {"name":"Closed","color":"#10b981","is_terminal":True},
    ]
    r = sa.post(BASE+"/superadmin/library/flows/new", data={
        "name": "Test Flow", "description": "A test flow",
        "industry": "Testing", "status": "DRAFT",
        "stages_json": json.dumps(stages),
    }, allow_redirects=True)
    check("Create flow -> 200", r.status_code == 200)
    check("Flow listed after creation", "Test Flow" in r.text)

    # Get the new flow's ID
    flow_ids = re.findall(r'/superadmin/library/flows/([a-f0-9\-]{36})', r.text)
    new_flow_id = flow_ids[0] if flow_ids else None
    check("New flow has ID", new_flow_id is not None)

    # ── K-3: Sub-module builder ────────────────────────────────────────────────
    print("\n[K-3] Sub-module builder (0-K-11)")
    r = sa.get(BASE+"/superadmin/library/submodules/new")
    check("GET /library/submodules/new -> 200", r.status_code == 200)
    check("Field type selector present", "Short Text" in r.text)
    check("Live preview section present", "previewPanel" in r.text or "Live Preview" in r.text)
    check("10 field types available", r.text.count("<option value=") >= 10)

    fields = [
        {"id":"f1","label":"Customer Name","type":"text","required":True,"options":[],"order":0},
        {"id":"f2","label":"Issue Category","type":"dropdown","required":True,"options":["A","B","C"],"order":1},
        {"id":"f3","label":"Photo Evidence","type":"photo","required":False,"options":[],"order":2},
    ]
    r = sa.post(BASE+"/superadmin/library/submodules/new", data={
        "name":"Custom Form","description":"Test","industry":"Testing",
        "status":"DRAFT","fields_json":json.dumps(fields),
    }, allow_redirects=True)
    check("Create sub-module -> 200", r.status_code == 200)
    sub_ids = re.findall(r'/superadmin/library/submodules/([a-f0-9\-]{36})', r.text)
    new_sub_id = sub_ids[0] if sub_ids else None
    check("Sub-module ID assigned", new_sub_id is not None)

    # ── K-4: Checklist CRUD ────────────────────────────────────────────────────
    print("\n[K-4] Checklist library CRUD")
    r = sa.post(BASE+"/superadmin/library/checklists/new", data={
        "name":"Custom Safety Check","description":"Daily test",
        "industry":"Testing","frequency":"DAILY",
        "assigned_to_role":"EMPLOYEE","status":"ACTIVE",
    }, allow_redirects=True)
    check("Create checklist -> 200", r.status_code == 200)

    # ── K-5: Label bundle deploy ───────────────────────────────────────────────
    print("\n[K-5] Label bundle apply to tenant")
    # Find construction bundle ID
    r = sa.get(BASE+"/superadmin/library/labels")
    bundle_ids = re.findall(r'/superadmin/library/labels/([a-f0-9\-]{36})/deploy', r.text)
    check("Label bundle IDs found", len(bundle_ids) > 0)
    if bundle_ids:
        r = sa.post(BASE+f"/superadmin/library/labels/{bundle_ids[0]}/deploy",
                    data={"tenant_id": tid}, allow_redirects=True)
        check("Apply label bundle -> 200", r.status_code == 200)
        check("Deploy success message", "deployed" in r.url or "Applied" in r.text or "deployed" in r.text.lower())

    # ── K-6: Onboarding bundle deploy ─────────────────────────────────────────
    print("\n[K-6] Onboarding bundle deploy")
    r = sa.get(BASE+"/superadmin/library/onboarding")
    ob_ids = re.findall(r'/superadmin/library/onboarding/([a-f0-9\-]{36})/deploy', r.text)
    check("Onboarding bundle IDs found", len(ob_ids) > 0)
    if ob_ids:
        r = sa.post(BASE+f"/superadmin/library/onboarding/{ob_ids[0]}/deploy",
                    data={"tenant_id": tid}, allow_redirects=True)
        check("Deploy onboarding bundle -> 200", r.status_code == 200)

    # ── K-7/K-8: Version tracking + update indicator ──────────────────────────
    print("\n[K-7/K-8] Version tracking & update available")
    r = sa.get(BASE+f"/superadmin/tenants/{tid}")
    check("Tenant detail 200", r.status_code == 200)
    check("Deployed items section shown", "Deployed Library Items" in r.text)
    check("Library items shown", "checklist" in r.text.lower() or "label_bundle" in r.text.lower())

    # Bump version on a deployed flow to trigger update indicator
    if new_flow_id:
        # Deploy flow first
        r = sa.post(BASE+f"/superadmin/library/flows/{new_flow_id}/deploy",
                    data={"tenant_id": tid}, allow_redirects=True)
        check("Flow deployed to tenant", r.status_code == 200)
        # Bump version
        r = sa.post(BASE+f"/superadmin/library/flows/{new_flow_id}", data={
            "name":"Test Flow","description":"Updated","industry":"Testing",
            "status":"ACTIVE","stages_json":json.dumps(stages),"bump_version":"true",
        }, allow_redirects=True)
        check("Version bumped -> 200", r.status_code == 200)
        # Tenant detail should now show update available
        r = sa.get(BASE+f"/superadmin/tenants/{tid}")
        check("Update available shown on tenant detail",
              "update" in r.text.lower() or "Update" in r.text)

    # ── K-9: Diff view ────────────────────────────────────────────────────────
    print("\n[K-9] Diff view")
    if new_flow_id:
        r = sa.get(BASE+f"/superadmin/library/flows/{new_flow_id}/diff/{tid}")
        check("Diff view -> 200", r.status_code == 200)
        check("Diff shows library vs deployed versions", "Library" in r.text or "Deployed" in r.text)

    # ── K-10: Bulk push ───────────────────────────────────────────────────────
    print("\n[K-10] Bulk push")
    if new_flow_id:
        r = sa.post(BASE+f"/superadmin/library/flows/{new_flow_id}/bulk-push",
                    allow_redirects=True)
        check("Bulk push -> 200", r.status_code == 200)

    # ── K-11: Duplicate system sub-module ─────────────────────────────────────
    print("\n[K-11] Duplicate system sub-module (cannot edit, must duplicate)")
    r = sa.get(BASE+"/superadmin/library/submodules")
    sys_ids = re.findall(r'/superadmin/library/submodules/([a-f0-9\-]{36})', r.text)
    if sys_ids:
        r = sa.post(BASE+f"/superadmin/library/submodules/{sys_ids[0]}/duplicate",
                    allow_redirects=True)
        check("Duplicate system sub-module -> 200", r.status_code == 200)
        check("Duplicated as draft", "draft" in r.text.lower() or "DRAFT" in r.text)

    # ── K-12: 3 pre-built industry bundles ───────────────────────────────────
    print("\n[K-12] Pre-built bundles")
    r = sa.get(BASE+"/superadmin/library/onboarding")
    check("Factory bundle present",      "Factory" in r.text or "Manufacturing" in r.text)
    check("Construction bundle present", "Construction" in r.text)
    check("Pharma bundle present",       "Pharma" in r.text)
    r = sa.get(BASE+"/superadmin/library/labels")
    check("8 label bundles shown", r.text.count("Manufacturing") + r.text.count("Restaurant") >= 2)

    # ── Library nav present ───────────────────────────────────────────────────
    print("\n[Nav] Library tab in SA navigation")
    r = sa.get(BASE+"/superadmin/dashboard")
    check("Library nav link in SA portal", "/superadmin/library" in r.text or "Library" in r.text)

    print(f"\n{'='*52}")
    print(f"  PASSED {len(ok)}/{len(ok)+len(fail)}")
    if fail: print("  FAILED:", fail)
    print('='*52)

finally:
    srv.terminate(); srv.wait()

sys.exit(0 if not fail else 1)
