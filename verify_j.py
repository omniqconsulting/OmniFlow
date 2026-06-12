import sqlite3
conn = sqlite3.connect('factoryos.db')
tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print("Tables:", tables)
cols = [c[1] for c in conn.execute("PRAGMA table_info(tenant_label_configs)").fetchall()]
print("tenant_label_configs columns:", cols)
conn.close()
