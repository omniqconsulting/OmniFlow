from app.database import create_tables, SessionLocal
create_tables()
from app.library_seeds import seed_library
db = SessionLocal()
seed_library(db)
db.close()

import sqlite3
conn = sqlite3.connect('factoryos.db')
tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print("Tables:", tables)
for tbl in ['library_flow_templates','library_flow_stages','library_submodule_definitions',
            'library_checklist_templates','library_label_bundles',
            'library_onboarding_bundles','tenant_deployed_items']:
    cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"  {tbl}: {cnt} rows")
conn.close()
print("OK")
