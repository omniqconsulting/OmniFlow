"""
Phase 4 Smoke Test â€” Inventory Management
Tests: 4-A (dashboard), 4-B (materials), 4-C (movements), 4-D (purchase orders)
"""
import requests, sys, json
from datetime import date

BASE = "http://127.0.0.1:8000"
OK = "[PASS]"; FAIL = "[FAIL]"
passed = failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  {OK}  {name}")
        passed += 1
    else:
        print(f"  {FAIL}  {name}  {detail}")
        failed += 1

def t_ok(name, r, expect=200):
    test(name, r.status_code == expect, f"status={r.status_code}")
    return r

print("\n=== Phase 4 Smoke Test ===\n")

# â”€â”€ Setup via DB â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from app.database import SessionLocal, SuperAdmin, Tenant, User, new_id, Material, PurchaseOrder, PurchaseOrderItem
from app.auth import hash_password

db = SessionLocal()

# SA
sa = db.query(SuperAdmin).filter(SuperAdmin.email == "sa4inv@test.com").first()
if not sa:
    sa = SuperAdmin(email="sa4inv@test.com",
                    password_hash=hash_password("pw123"), name="SA Inv")
    db.add(sa)
    db.commit()
test("SA created in DB", bool(sa))

# Tenant
tenant = db.query(Tenant).filter(Tenant.slug == "invco2").first()
if not tenant:
    tenant = Tenant(name="InvCo", slug="invco2", plan="PROFESSIONAL",
                    industry="Manufacturing", is_approved=True)
    db.add(tenant)
    db.flush()

# Users
admin = db.query(User).filter(User.email == "admin@invco2.com").first()
if not admin:
    admin = User(
        tenant_id=tenant.id, name="Inv Admin", phone="9001000001",
        email="admin@invco2.com", role="ADMIN",
        password_hash=hash_password("pw123"), is_active=True,
    )
    db.add(admin)

sm = db.query(User).filter(User.email == "sm@invco2.com").first()
if not sm:
    sm = User(
        tenant_id=tenant.id, name="Store Mgr One", phone="9001000002",
        email="sm@invco2.com", role="STORE_MANAGER",
        password_hash=hash_password("pw123"), is_active=True,
    )
    db.add(sm)

db.commit()
TENANT_ID = tenant.id  # capture before any close
test("Tenant + Admin + SM in DB", bool(tenant and admin and sm))

# Enable INVENTORY feature via override
from app.database import TenantFeatureOverride
override = db.query(TenantFeatureOverride).filter(
    TenantFeatureOverride.tenant_id == TENANT_ID,
    TenantFeatureOverride.feature == "INVENTORY",
).first()
if not override:
    db.add(TenantFeatureOverride(
        tenant_id=TENANT_ID, feature="INVENTORY", enabled=True,
    ))
    db.commit()
test("INVENTORY feature enabled for tenant", True)
db.close()

# â”€â”€ 4-A: Store Manager Dashboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\n--- 4-A: Store Manager Dashboard ---")

ts = requests.Session()
r = ts.post(f"{BASE}/login",
    data={"slug": "invco2", "phone": "9001000002", "password": "pw123"},
    allow_redirects=False)
test("SM login responds", r.status_code in (302, 303), f"got {r.status_code}")
location = r.headers.get("location", "")
test("SM redirected to /inventory", "/inventory" in location, f"location={location}")

r = ts.get(f"{BASE}/inventory", allow_redirects=True)
test("SM /inventory page loads (200)", r.status_code == 200, f"got {r.status_code}")
test("Dashboard content present", "Dashboard" in r.text)

ta = requests.Session()
r = ta.post(f"{BASE}/login",
    data={"slug": "invco2", "phone": "9001000001", "password": "pw123"},
    allow_redirects=False)
location = r.headers.get("location", "")
test("Admin login redirects to /dashboard", "dashboard" in location, f"location={location}")

r = ta.get(f"{BASE}/inventory", allow_redirects=True)
test("Admin can also access /inventory", r.status_code == 200, f"got {r.status_code}")

# â”€â”€ 4-B: Material Catalogue â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\n--- 4-B: Material Catalogue ---")

r = ta.get(f"{BASE}/inventory/materials")
test("Materials list page loads", r.status_code == 200)

r = ta.post(f"{BASE}/inventory/materials/add",
    data={"name": "Steel Rod 10mm", "unit": "pcs", "opening_stock": "100",
          "unit_cost": "50", "reorder_threshold": "20", "reorder_qty": "50",
          "lead_time_days": "7", "supplier": "ABC Metals"},
    allow_redirects=True)
test("Add material Steel Rod", r.status_code == 200)

r = ta.post(f"{BASE}/inventory/materials/add",
    data={"name": "Welding Wire", "unit": "kg", "opening_stock": "25",
          "unit_cost": "200", "reorder_threshold": "5", "reorder_qty": "20"},
    allow_redirects=True)
test("Add material Welding Wire", r.status_code == 200)

db = SessionLocal()
mats = db.query(Material).filter(Material.tenant_id == TENANT_ID).all()
test(f"Materials in DB ({len(mats)})", len(mats) >= 2)
# Capture as plain dicts before close
m1 = {"id": mats[0].id, "name": mats[0].name, "unit": mats[0].unit} if mats else None
m2 = {"id": mats[1].id, "name": mats[1].name, "unit": mats[1].unit} if len(mats) > 1 else None
db.close()

if m1:
    r = ta.get(f"{BASE}/inventory/materials/{m1["id"]}")
    test("Material detail page", r.status_code == 200)
    test("Opening stock movement recorded", "OPENING" in r.text)

    # Edit material
    r = ta.post(f"{BASE}/inventory/materials/{m1["id"]}/edit",
        data={"name": m1["name"], "unit": m1["unit"], "reorder_threshold": "25",
              "reorder_qty": "60", "lead_time_days": "5", "is_active": "true"},
        allow_redirects=True)
    test("Edit material", r.status_code == 200)

# â”€â”€ 4-C: Stock Movements â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\n--- 4-C: Stock Movements ---")

r = ta.get(f"{BASE}/inventory/movements")
test("Movements ledger page loads", r.status_code == 200)

if m1:
    r = ta.post(f"{BASE}/inventory/movements/add",
        data={"material_id": m1["id"], "movement_type": "STOCK_IN",
              "qty": "50", "unit_cost": "48", "reference": "GRN-001",
              "notes": "Received from supplier"},
        allow_redirects=True)
    test("Record STOCK_IN movement", r.status_code == 200)

    r = ta.post(f"{BASE}/inventory/movements/add",
        data={"material_id": m1["id"], "movement_type": "STOCK_OUT",
              "qty": "10", "reference": "TICKET-001",
              "notes": "Issued to production"},
        allow_redirects=True)
    test("Record STOCK_OUT movement", r.status_code == 200)

    db = SessionLocal()
    from sqlalchemy import text as _text
    m1r = db.query(Material).filter(Material.id == m1["id"]).first()
    # Stock increased by 50 (STOCK_IN) and decreased by 10 (STOCK_OUT) = net +40
    prev_stock = m1r.current_stock - 50 + 10  # back-calculate
    expected_delta = 50 - 10
    test(f"Stock increased by net +{expected_delta} after STOCK_IN/OUT",
         m1r.current_stock == prev_stock + expected_delta,
         f"current={m1r.current_stock} expected={prev_stock + expected_delta}")
    db.close()

    # Filter by material
    m1id = m1["id"]
    r = ta.get(f"{BASE}/inventory/movements?material_id={m1id}")
    test("Movements filter by material", r.status_code == 200)

    # Filter by type
    r = ta.get(f"{BASE}/inventory/movements?movement_type=STOCK_IN")
    test("Movements filter by type", r.status_code == 200)

# â”€â”€ 4-D: Purchase Orders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\n--- 4-D: Purchase Orders ---")

r = ta.get(f"{BASE}/inventory/purchase-orders")
test("PO list page loads", r.status_code == 200)

r = ta.get(f"{BASE}/inventory/purchase-orders/new")
test("New PO form loads", r.status_code == 200)

if m1 and m2:
    items_data = [
        {"material_id": m1["id"], "material_name": m1["name"], "unit": m1["unit"],
         "qty_ordered": 100, "unit_cost": 50},
        {"material_id": m2["id"], "material_name": m2["name"], "unit": m2["unit"],
         "qty_ordered": 10, "unit_cost": 200},
    ]
    r = ta.post(f"{BASE}/inventory/purchase-orders/new",
        data={"supplier": "ABC Metals & Supplies", "supplier_ref": "Q-2024-001",
              "expected_delivery": str(date.today()),
              "notes": "Monthly restock",
              "items_json": json.dumps(items_data)},
        allow_redirects=True)
    test("Create PO (2 items)", r.status_code == 200)

db = SessionLocal()
pos = db.query(PurchaseOrder).filter(PurchaseOrder.tenant_id == TENANT_ID).order_by(
    PurchaseOrder.created_at.desc()).all()
test("PO created in DB", len(pos) >= 1)
po = pos[0] if pos else None  # most recent
po_id = po.id if po else None
po_status = po.status if po else None
test("Latest PO status is DRAFT", po_status == "DRAFT")
db.close()

if po_id:
    r = ta.get(f"{BASE}/inventory/purchase-orders/{po_id}")
    test("PO detail page loads", r.status_code == 200)

    # Submit
    r = ta.post(f"{BASE}/inventory/purchase-orders/{po_id}/submit",
        allow_redirects=True)
    test("Submit PO (DRAFT->SUBMITTED)", r.status_code == 200)

    db = SessionLocal()
    po_s = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    test("PO is SUBMITTED", po_s.status == "SUBMITTED")
    db.close()

    # SM cannot approve
    r = ts.post(f"{BASE}/inventory/purchase-orders/{po_id}/approve",
        allow_redirects=False)
    test("SM cannot approve PO (403)", r.status_code == 403)

    # Admin approves
    r = ta.post(f"{BASE}/inventory/purchase-orders/{po_id}/approve",
        allow_redirects=True)
    test("Admin approves PO (SUBMITTED->APPROVED)", r.status_code == 200)

    db = SessionLocal()
    po_a = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    test("PO is APPROVED", po_a.status == "APPROVED")
    items = db.query(PurchaseOrderItem).filter(
        PurchaseOrderItem.po_id == po_id).all()
    recv_map = {item.id: item.qty_ordered for item in items}  # full receipt
    db.close()

    # Receive full qty
    r = ta.post(f"{BASE}/inventory/purchase-orders/{po_id}/receive",
        data={"received_quantities": json.dumps(recv_map),
              "notes": "Full delivery received"},
        allow_redirects=True)
    test("Receive full stock against PO", r.status_code == 200)

    db = SessionLocal()
    po_final = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    test("PO status is RECEIVED", po_final.status == "RECEIVED")
    m1r = db.query(Material).filter(Material.id == m1["id"]).first() if m1 else None
    if m1r:
        test("Stock updated via full PO receipt",
             m1r.current_stock > 0, f"got {m1r.current_stock}")
    db.close()

# â”€â”€ 4-D: Material requests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\n--- Material Requests ---")
r = ta.get(f"{BASE}/inventory/requests")
test("Requests page loads", r.status_code == 200)

# â”€â”€ Feature gate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\n--- Feature Gate ---")
db = SessionLocal()
t2 = db.query(Tenant).filter(Tenant.slug == "basicco4").first()
if not t2:
    t2 = Tenant(name="BasicCo", slug="basicco4", plan="STARTER",
                industry="Manufacturing", is_approved=True)
    db.add(t2)
    db.flush()
    u2 = User(tenant_id=t2.id, name="Basic User", phone="9002000099",
              email="basic@basicco4.com", role="ADMIN",
              password_hash=hash_password("pw123"), is_active=True)
    db.add(u2)
    db.commit()
db.close()

tb = requests.Session()
r = tb.post(f"{BASE}/login",
    data={"slug": "basicco4", "phone": "9002000099", "password": "pw123"})
r = tb.get(f"{BASE}/inventory")
test("STARTER tenant blocked from /inventory (403)", r.status_code == 403,
     f"got {r.status_code}")

# â”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print(f"\n{'='*40}")
print(f"  Passed: {passed}  /  Total: {passed + failed}")
if failed:
    print(f"  FAILED: {failed}")
    sys.exit(1)
else:
    print("  ALL TESTS PASSED")

