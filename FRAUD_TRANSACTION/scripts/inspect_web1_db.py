import sqlite3
p='web1/database.db'
conn=sqlite3.connect(p)
cur=conn.cursor()
print('Tables in web1 DB:')
for row in cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
    print(row)

print('\nPRAGMA table_info(users):')
for r in cur.execute('PRAGMA table_info(users)'):
    print(r)
conn.close()
