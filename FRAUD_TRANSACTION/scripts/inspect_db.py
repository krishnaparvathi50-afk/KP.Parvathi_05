import sqlite3
from pathlib import Path
p = Path('web1/database.db')
if not p.exists():
    print('DB not found:', p)
    raise SystemExit(2)
conn = sqlite3.connect(str(p))
cur = conn.cursor()
print('Tables:')
for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(' -', row[0])

print('\nSchema for users:')
for row in cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"):
    print(row[0])

print('\nPRAGMA table_info(users):')
for row in cur.execute('PRAGMA table_info(users)'):
    print(row)

conn.close()
