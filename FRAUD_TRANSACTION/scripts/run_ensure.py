import sqlite3
from pathlib import Path
DB='database.db'
print('Before:')
conn=sqlite3.connect(DB)
for r in conn.execute("PRAGMA table_info(users)"):
    print(r)
conn.close()

# Import the app to run ensure_users_schema (it runs on import)
import importlib.util
spec = importlib.util.spec_from_file_location('app_mod', 'web1/app.py')
app_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_mod)

print('\nAfter:')
conn=sqlite3.connect(DB)
for r in conn.execute("PRAGMA table_info(users)"):
    print(r)
conn.close()
