"""One-time migration: assign employee_id (EMP-XXXX) to all existing users that lack one."""
import sqlite3, os

DB = os.path.join(os.path.dirname(__file__), "omniflow.db")

conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("SELECT DISTINCT tenant_id FROM users WHERE is_deleted=0")
tenant_ids = [r[0] for r in cur.fetchall()]

total = 0
for tid in tenant_ids:
    cur.execute(
        "SELECT id FROM users WHERE tenant_id=? AND (employee_id IS NULL OR employee_id='') AND is_deleted=0 ORDER BY created_at",
        (tid,)
    )
    users = [r[0] for r in cur.fetchall()]
    for i, uid in enumerate(users, start=1):
        eid = f"EMP-{i:04d}"
        cur.execute("UPDATE users SET employee_id=? WHERE id=?", (eid, uid))
        total += 1

conn.commit()
conn.close()
print(f"Assigned employee_id to {total} users across {len(tenant_ids)} tenants.")
