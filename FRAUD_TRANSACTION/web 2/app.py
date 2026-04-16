from datetime import datetime
import os
from functools import wraps
import sqlite3
from pathlib import Path

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

from model import predict_fraud

app = Flask(__name__)
app.secret_key = "secret"

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
ROOT_DB = ROOT_DIR / "database.db"
LEGACY_DB = BASE_DIR / "database.db"
DB_PATH = ROOT_DB if ROOT_DB.exists() else LEGACY_DB


def get_db():
    return sqlite3.connect(str(DB_PATH))


def is_web2_admin():
    return bool(session.get("web2_admin"))


def admin_required(view_fn):
    @wraps(view_fn)
    def wrapped(*args, **kwargs):
        if not is_web2_admin():
            return redirect(url_for("login"))
        return view_fn(*args, **kwargs)

    return wrapped


def validate_web2_admin(username: str, password: str):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT password FROM admins WHERE name=? AND is_active=1 LIMIT 1", (username,))
    row = cur.fetchone()
    db.close()
    if row and row[0]:
        return check_password_hash(row[0], password)

    admin_name = os.environ.get("WEB2_ADMIN_NAME", "admin")
    admin_password = os.environ.get("WEB2_ADMIN_PASSWORD", "Admin@123")
    return username == admin_name and password == admin_password


def table_has_column(cur, table, column):
    """Return True if the specified column exists in the table."""
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def classify_ml(*, risk):
    """Map numeric risk score to human-readable ML verdict."""
    if risk is None:
        return "UNKNOWN"
    if risk >= 85:
        return "HIGH"
    if risk >= 50:
        return "MEDIUM"
    return "LOW"


def fetch_transactions(*, show_all=False, limit=50, offset=0):
    """Fetch transactions with optional pagination and graceful risk fallback."""
    db = get_db()
    cur = db.cursor()
    has_risk = table_has_column(cur, "transactions", "risk")
    risk_expr = "risk" if has_risk else "NULL AS risk"
    query = f"SELECT sender,receiver,amount,ip,timestamp,status,{risk_expr} FROM transactions ORDER BY id DESC"
    params = []
    if not show_all and limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    if offset:
        query += " OFFSET ?"
        params.append(offset)
    cur.execute(query, params)
    rows = cur.fetchall()

    registered_cache: dict[str, bool] = {}

    def is_registered(mobile: str) -> bool:
        if mobile in registered_cache:
            return registered_cache[mobile]
        cur.execute("SELECT 1 FROM users WHERE mobile=? LIMIT 1", (mobile,))
        registered_cache[mobile] = cur.fetchone() is not None
        return registered_cache[mobile]

    transactions = []
    for sender, receiver, amount, ip, timestamp, status, risk in rows:
        if risk is None:
            try:
                _, risk = predict_fraud(amount)
            except Exception:
                risk = None

        receiver_registered = is_registered(receiver)

        # Sending to an unregistered receiver is always treated as fraud.
        if not receiver_registered:
            status_display = "FRAUD"
            if risk is None or risk < 85:
                risk = 85.0
        else:
            status_display = status

        ml_label = classify_ml(risk=risk)

        transactions.append(
            dict(
                sender=sender,
                receiver=receiver,
                amount=amount,
                ip=ip,
                timestamp=timestamp,
                status=status_display,
                risk=risk,
                ml_label=ml_label,
            )
        )

    db.close()
    return transactions


def get_admin_unread_notification_count():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM admin_notifications WHERE is_read=0")
    count = cur.fetchone()[0]
    db.close()
    return count


def list_admin_notifications(limit=10):
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT message, timestamp, source FROM admin_notifications WHERE is_read=0 ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    db.close()
    return rows


def init_db():
    db = get_db()
    cur = db.cursor()

    # Create users table if missing
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        mobile TEXT UNIQUE,
        is_blocked INTEGER DEFAULT 0
    )
    """
    )

    # Create transactions table if missing
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT,
        receiver TEXT,
        amount REAL,
        ip TEXT,
        timestamp TEXT,
        status TEXT,
        risk REAL
    )
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS admin_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT,
        message TEXT,
        timestamp TEXT,
        is_read INTEGER DEFAULT 0
    )
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        email TEXT UNIQUE,
        password TEXT,
        is_active INTEGER DEFAULT 1
    )
    """
    )

    db.commit()

    # Populate sample users if table empty
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    if count == 0:
        sample_users = [f"user{i}" for i in range(1, 51)]
        for i, u in enumerate(sample_users):
            mobile = str(6000000000 + i)
            try:
                cur.execute("INSERT INTO users (username, mobile, is_blocked) VALUES (?,?,0)", (u, mobile))
            except sqlite3.IntegrityError:
                pass
        db.commit()

    # Seed default admin if not present
    seed_name = os.environ.get("WEB2_ADMIN_NAME", "admin").strip()
    seed_email = os.environ.get("WEB2_ADMIN_EMAIL", "admin@fraudwatch.local").strip().lower()
    seed_password = os.environ.get("WEB2_ADMIN_PASSWORD", "Admin@123")
    if seed_name and seed_password:
        cur.execute("SELECT id FROM admins WHERE name=? LIMIT 1", (seed_name,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO admins (name, email, password, is_active) VALUES (?, ?, ?, 1)",
                (seed_name, seed_email, generate_password_hash(seed_password)),
            )
            db.commit()

    db.close()


init_db()


# ---------------- AUTH ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_web2_admin():
        return redirect(url_for("transaction"))

    if request.method == 'POST':
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if validate_web2_admin(username, password):
            session["web2_admin"] = username
            session["web2_admin_login_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return redirect(url_for("transaction"))
        return render_template("login.html", error="Invalid admin name or password.")

    return render_template("login.html")


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get("username", "").strip()
        reset_key = request.form.get("reset_key", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        expected_key = os.environ.get("WEB2_RESET_KEY", "RESET@123")
        if reset_key != expected_key:
            return render_template("forgot_password.html", error="Invalid reset key.")
        if not username:
            return render_template("forgot_password.html", error="Enter admin name.")
        if new_password != confirm_password:
            return render_template("forgot_password.html", error="Passwords do not match.")
        if len(new_password) < 6:
            return render_template("forgot_password.html", error="Password must be at least 6 characters.")

        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT id FROM admins WHERE name=? LIMIT 1", (username,))
        row = cur.fetchone()
        if not row:
            db.close()
            return render_template("forgot_password.html", error="Admin not found.")

        cur.execute(
            "UPDATE admins SET password=? WHERE name=?",
            (generate_password_hash(new_password), username),
        )
        db.commit()
        db.close()
        return render_template("login.html", success="Password updated. Please login.")

    return render_template("forgot_password.html")


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- HOME -> TRANSACTION PAGE ----------------
@app.route('/')
@admin_required
def transaction():
    show_all = request.args.get('all') == '1'
    transactions = fetch_transactions(show_all=show_all, limit=50)
    return render_template(
        "transaction.html",
        transactions=transactions,
        notif_count=get_admin_unread_notification_count(),
        admin_name=session.get("web2_admin", "admin"),
    )


@app.route('/transactions')
@admin_required
def transactions_api():
    """JSON endpoint to return transactions with offset/limit for 'Load more' support."""
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0
    try:
        limit = int(request.args.get('limit', 50))
    except ValueError:
        limit = 50

    transactions = fetch_transactions(show_all=False, limit=limit, offset=offset)
    return jsonify(transactions)


@app.route('/api/admin_notifications/unread')
@admin_required
def admin_unread_notifications():
    return jsonify({"count": get_admin_unread_notification_count()})


@app.route('/api/admin_notifications/list')
@admin_required
def admin_notifications_list():
    rows = list_admin_notifications(limit=10)
    return jsonify(
        {
            "notifications": [
                {"message": r[0], "time": r[1], "source": r[2] if len(r) > 2 else "web1"}
                for r in rows
            ]
        }
    )


@app.route('/api/admin_notifications/clear', methods=['POST'])
@admin_required
def clear_admin_notifications():
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE admin_notifications SET is_read=1 WHERE is_read=0")
    db.commit()
    db.close()
    return jsonify({"success": True})


if __name__ == '__main__':
    app.run(debug=True, port=8000, use_reloader=False)
