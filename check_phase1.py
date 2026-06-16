import sqlite3
conn = sqlite3.connect('omniflow.db')
cur = conn.cursor()

checks = [
    ('customers', ['id','tenant_id','name','contact_person','phone','email','address','notes','is_active','is_deleted','created_by_id','created_at','updated_at']),
    ('end_products', ['id','tenant_id','name','sku_code','unit','description','is_active','is_deleted','created_by_id','created_at','updated_at']),
    ('custom_reference_lists', ['id','tenant_id','list_name','is_active','is_deleted','created_by_id','created_at']),
    ('custom_reference_items', ['id','list_id','tenant_id','value','sort_order','is_active','is_deleted','created_at']),
    ('linked_entity_references', ['id','tenant_id','parent_type','parent_id','entity_type','entity_id','entity_label','custom_text','created_by_id','created_at']),
    ('users', ['employee_id','joining_date','address','status','terminated_at','branch_id']),
    ('tickets', ['ticket_category','evidence_required']),
    ('fms_stages', ['evidence_required']),
    ('library_flow_stages', ['evidence_required']),
    ('checklist_assignments', ['delay_reason','evidence_required']),
    ('checklist_templates', ['evidence_required']),
]

all_ok = True
for table, expected_cols in checks:
    cur.execute(f'PRAGMA table_info({table})')
    existing = {r[1] for r in cur.fetchall()}
    missing = [c for c in expected_cols if c not in existing]
    if missing:
        print(f'MISSING in {table}: {missing}')
        all_ok = False
    else:
        print(f'OK  {table}')

conn.close()
print()
print('Phase 1 schema: ALL OK' if all_ok else 'Phase 1 schema: ISSUES FOUND')
