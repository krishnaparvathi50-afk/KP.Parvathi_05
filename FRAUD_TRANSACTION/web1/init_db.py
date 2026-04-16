import sqlite3

conn = sqlite3.connect("database.db")
cur = conn.cursor()

# USERS TABLE
cur.execute("DROP TABLE IF EXISTS users")
cur.execute("""
CREATE TABLE users(
id INTEGER PRIMARY KEY AUTOINCREMENT,
username TEXT UNIQUE,
email TEXT UNIQUE,
mobile TEXT UNIQUE,
password TEXT
)
""")

# TRANSACTIONS TABLE
cur.execute("""
CREATE TABLE IF NOT EXISTS transactions(
id INTEGER PRIMARY KEY AUTOINCREMENT,
sender TEXT,
receiver TEXT,
amount REAL,
ip TEXT,
timestamp TEXT,
status TEXT
)
""")

conn.commit()
conn.close()