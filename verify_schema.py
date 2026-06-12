import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app.database import create_tables, engine
create_tables()
from sqlalchemy import inspect
insp = inspect(engine)
print("Tables:", insp.get_table_names())
print("Tenant cols:", [c["name"] for c in insp.get_columns("tenants")])
print("SuperAdmin cols:", [c["name"] for c in insp.get_columns("super_admins")])
print("SCHEMA OK")
