from pathlib import Path
import sqlite3
from datetime import datetime
import os
import random
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import re

from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "supersecretkey"
OTP_EXPIRY_SECONDS = 300

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

if not UPLOAD_FOLDER.exists():
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

ROOT_DB = ROOT_DIR / "database.db"
LEGACY_DB = BASE_DIR / "database.db"
DB_PATH = ROOT_DB if ROOT_DB.exists() else LEGACY_DB

# Load environment variables from project .env (project root)
env_path = ROOT_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()


def get_db():
    # Adding timeout=20 to wait for the database if it's currently locked
    conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn


def get_table_columns(table: str):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        return cols
    finally:
        conn.close()


def ensure_users_schema():
    """Ensure common columns exist in the users table expected by the app.
    Adds `email` and `password` columns if they're missing.
    """
    cols = get_table_columns("users")
    if not cols:
        # No users table present in this DB; nothing to do here.
        return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    altered = False
    if "email" not in cols:
        try:
            cur.execute("ALTER TABLE users ADD COLUMN email TEXT UNIQUE")
            altered = True
            print("Added 'email' column to users table")
        except Exception as e:
            print("Failed to add email column:", e)
            # Try adding without UNIQUE constraint (SQLite cannot add UNIQUE via ALTER TABLE)
            try:
                cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
                altered = True
                print("Added 'email' column (without UNIQUE) to users table")
            except Exception as e2:
                print("Failed to add email column without UNIQUE:", e2)
    if "password" not in cols:
        try:
            cur.execute("ALTER TABLE users ADD COLUMN password TEXT")
            altered = True
            print("Added 'password' column to users table")
        except Exception as e:
            print("Failed to add password column:", e)
    if altered:
        conn.commit()
    conn.close()


# Ensure DB schema is compatible on startup
def ensure_tables_exist():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    # Create users table if missing with common columns
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            email TEXT UNIQUE,
            mobile TEXT UNIQUE,
            password TEXT,
            is_blocked INTEGER DEFAULT 0
        )
        """
    )
    # Create transactions table if missing with common columns
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions(
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
    # Create notifications table if missing
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            message TEXT,
            timestamp TEXT,
            is_read INTEGER DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admins(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            email TEXT UNIQUE,
            password TEXT,
            is_active INTEGER DEFAULT 1
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            message TEXT,
            timestamp TEXT,
            is_read INTEGER DEFAULT 0
        )
        """
    )

    # Seed a default Web-1 admin so admin icon login works out of the box.
    admin_name = os.environ.get("WEB1_ADMIN_NAME", "admin").strip()
    admin_email = os.environ.get("WEB1_ADMIN_EMAIL", "admin@fraudwatch.local").strip().lower()
    admin_password = os.environ.get("WEB1_ADMIN_PASSWORD", "Admin@123")
    if admin_name and admin_email and admin_password:
        cur.execute("SELECT id FROM admins WHERE email=?", (admin_email,))
        exists = cur.fetchone()
        if not exists:
            cur.execute(
                "INSERT INTO admins (name, email, password) VALUES (?, ?, ?)",
                (admin_name, admin_email, generate_password_hash(admin_password)),
            )
            print(f"Seeded Web-1 admin: {admin_email}")
    conn.commit()
    conn.close()


ensure_tables_exist()
ensure_users_schema()


def ensure_transactions_schema():
    cols = get_table_columns("transactions")
    if not cols:
        return
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    altered = False
    if "twilio_sid" not in cols:
        try:
            cur.execute("ALTER TABLE transactions ADD COLUMN twilio_sid TEXT")
            altered = True
            print("Added 'twilio_sid' column to transactions table")
        except Exception as e:
            print("Failed to add twilio_sid column:", e)
    if "twilio_error" not in cols:
        try:
            cur.execute("ALTER TABLE transactions ADD COLUMN twilio_error TEXT")
            altered = True
            print("Added 'twilio_error' column to transactions table")
        except Exception as e:
            print("Failed to add twilio_error column:", e)
    if altered:
        conn.commit()
    conn.close()


ensure_transactions_schema()


def normalize_phone(number: str) -> str:
    """Normalize a phone number to E.164-like format.

    Rules:
    - Strip spaces, dashes and parentheses.
    - Reject numbers containing letters or placeholders like 'X' or '*'.
    - If the cleaned value already starts with '+', validate length and return it.
    - If the cleaned value is 10 digits, assume India and return '+91' + digits.
    - If the cleaned value starts with '91' and is 12 digits, prepend '+' (India full without +).
    - Otherwise prepend '+' to numeric values as a fallback.
    - Return empty string on invalid input or if resulting digits exceed 15 (E.164 max).
    """
    if not number:
        return ""
    raw = str(number).strip()
    cleaned = re.sub(r"[\s\-()]+", "", raw)

    # Reject placeholders or unexpected characters
    if re.search(r"[xX*]", cleaned) or re.search(r"[^0-9+]", cleaned):
        return ""

    # Already in E.164 form
    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if not digits.isdigit():
            return ""
        if 7 <= len(digits) <= 15:
            return cleaned
        return ""

    # Digits only from here
    if not cleaned.isdigit():
        return ""

    # 10-digit numbers -> assume India +91
    if len(cleaned) == 10:
        candidate = "+91" + cleaned
        return candidate

    # If user entered '91' + 10 digits without plus
    if cleaned.startswith("91") and len(cleaned) == 12:
        return "+" + cleaned

    # Fallback: prepend plus and validate length
    if 1 <= len(cleaned) <= 15:
        candidate = "+" + cleaned
        if 7 <= len(candidate) - 1 <= 15:
            return candidate

    return ""


def send_alert_sms(to_number: str, body: str) -> bool:
    """Send an SMS via Twilio.

    Returns tuple `(success: bool, info: str)` where `info` is the message SID on success
    or an error message on failure.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")

    if not (account_sid and auth_token and from_number):
        print("Twilio not configured. Skipping SMS alert.")
        return False, "Twilio not configured"

    # Normalize recipient number to E.164-like. If invalid, abort.
    cleaned = normalize_phone(to_number)
    if not cleaned:
        print("Invalid recipient phone number, unable to normalize:", to_number)
        return False, "Invalid recipient phone number"

    try:
        client = Client(account_sid, auth_token)
        resp = client.messages.create(body=body, from_=from_number, to=cleaned)
        sid = getattr(resp, 'sid', None)
        print(f"Twilio message sent, SID={sid}")
        return True, sid or ""
    except TwilioRestException as e:
        # Twilio-specific details
        try:
            err = f"status={e.status} code={e.code} msg={e.msg}"
        except Exception:
            err = str(e)
        print("TwilioRestException when sending SMS:", err)
        return False, err
    except Exception as e:
        print("Failed to send SMS via Twilio:", e)
        return False, str(e)


def ensure_twilio_config():
    """Validate Twilio credentials at startup and report problems.
    Does a lightweight account fetch to verify credentials and connectivity.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not (account_sid and auth_token and from_number):
        print("Twilio not configured (missing env vars). Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in .env")
        return
    try:
        client = Client(account_sid, auth_token)
        # Try to fetch account to validate creds
        acct = client.api.accounts(account_sid).fetch()
        print(f"Twilio account OK: {acct.friendly_name} ({acct.sid})")
    except TwilioRestException as e:
        print("Twilio credentials invalid or API error:")
        try:
            print("Status:", e.status, "Code:", e.code, "Message:", e.msg)
        except Exception:
            print(str(e))
    except Exception as e:
        print("Unexpected error validating Twilio config:", e)


# Validate Twilio on startup so problems are visible in logs
ensure_twilio_config()


def is_logged_in():
    return bool(session.get("user"))


def is_admin_session():
    return session.get("auth_type") == "admin"


def create_admin_notification(message: str, source: str = "web1"):
    db = get_db()
    try:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO admin_notifications (source, message, timestamp) VALUES (?, ?, ?)",
            (source, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        db.commit()
    finally:
        db.close()


def send_admin_otp_email(to_email: str, otp: str):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user or "no-reply@fraudwatch.local")

    if not (smtp_host and smtp_user and smtp_password):
        return False, "SMTP is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD."

    msg = EmailMessage()
    msg["Subject"] = "Fraud Watch Admin OTP"
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content(
        f"Your Fraud Watch admin OTP is: {otp}\n\n"
        f"This OTP expires in {OTP_EXPIRY_SECONDS // 60} minutes."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
        return True, ""
    except Exception as e:
        return False, str(e)


def get_notification_count():
    if not is_logged_in():
        return 0
    db = get_db()
    try:
        cur = db.cursor()
        if is_admin_session():
            cur.execute("SELECT COUNT(*) FROM admin_notifications WHERE is_read=0")
            return cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM notifications WHERE username=? AND is_read=0", (session["user"],))
        return cur.fetchone()[0]
    finally:
        db.close()


def get_notification_rows():
    if not is_logged_in():
        return []
    db = get_db()
    try:
        cur = db.cursor()
        if is_admin_session():
            cur.execute(
                "SELECT message, timestamp FROM admin_notifications WHERE is_read=0 ORDER BY id DESC LIMIT 10"
            )
        else:
            cur.execute(
                "SELECT message, timestamp FROM notifications WHERE username=? AND is_read=0 ORDER BY id DESC LIMIT 10",
                (session["user"],),
            )
        return cur.fetchall()
    finally:
        db.close()


@app.route("/")
def home():
    if is_logged_in():
        return redirect(url_for("dashboard"))
    return render_template("home.html")


@app.route("/about")
def about():
    if is_logged_in():
        return redirect(url_for("dashboard"))
    return redirect(url_for("home", panel="about") + "#insights")


@app.route("/contact")
def contact():
    if is_logged_in():
        return redirect(url_for("dashboard"))
    return redirect(url_for("home", panel="contact") + "#insights")


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        role = request.form.get("role", "user").strip().lower()
        db = get_db()
        try:
            cur = db.cursor()
            if role == "admin":
                admin_email = request.form.get("admin_email", "").strip().lower()
                admin_password = request.form.get("admin_password", "")
                otp_input = request.form.get("otp", "").strip()

                # Step 2: verify OTP after password check.
                if otp_input:
                    pending = session.get("pending_admin_otp")
                    if not pending:
                        return render_template("login.html", role="admin", admin_stage="password", error="OTP session expired. Login again.")
                    if pending.get("email") != admin_email:
                        return render_template("login.html", role="admin", admin_stage="password", error="OTP email mismatch. Try again.")
                    if datetime.now().timestamp() > pending.get("expires_at", 0):
                        session.pop("pending_admin_otp", None)
                        return render_template("login.html", role="admin", admin_stage="password", error="OTP expired. Login again.")
                    if otp_input != pending.get("otp"):
                        return render_template("login.html", role="admin", admin_stage="otp", admin_email=admin_email, error="Invalid OTP.")

                    cur.execute("SELECT * FROM admins WHERE email=? AND is_active=1", (admin_email,))
                    admin = cur.fetchone()
                    if not admin:
                        session.pop("pending_admin_otp", None)
                        return render_template("login.html", role="admin", admin_stage="password", error="Admin account not found.")

                    session.pop("pending_admin_otp", None)
                    session["user"] = admin["name"]
                    session["auth_type"] = "admin"
                    session["admin_email"] = admin_email
                    return redirect(url_for("dashboard"))

                # Step 1: verify credentials and send OTP.
                cur.execute("SELECT * FROM admins WHERE email=? AND is_active=1", (admin_email,))
                admin = cur.fetchone()
                if not admin or not check_password_hash(admin["password"], admin_password):
                    return render_template("login.html", role="admin", admin_stage="password", error="Invalid admin email or password.")

                otp = f"{random.randint(0, 999999):06d}"
                sent, err = send_admin_otp_email(admin_email, otp)
                if not sent:
                    return render_template("login.html", role="admin", admin_stage="password", error=f"OTP send failed: {err}")

                session["pending_admin_otp"] = {
                    "email": admin_email,
                    "otp": otp,
                    "expires_at": datetime.now().timestamp() + OTP_EXPIRY_SECONDS,
                }
                return render_template(
                    "login.html",
                    role="admin",
                    admin_stage="otp",
                    admin_email=admin_email,
                    success="OTP sent to your admin email.",
                )

            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            cur.execute("SELECT * FROM users WHERE username=?", (username,))
            user = cur.fetchone()
            if user and user["password"] and check_password_hash(user["password"], password):
                session["user"] = username
                session["auth_type"] = "user"
                return redirect(url_for("dashboard"))
        finally:
            db.close()

        return render_template("login.html", role="user", error="Invalid Username or Password")

    return render_template("login.html", role="user", admin_stage="password")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        mobile_input = request.form.get("mobile", "").strip()
        mobile = normalize_phone(mobile_input)
        if not mobile:
            return render_template("register.html", error="Enter a valid mobile number (e.g. +919629451234)")
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match!")

        hashed_password = generate_password_hash(password)

        db = get_db()
        try:
            cur = db.cursor()
            cur.execute(
                "SELECT * FROM users WHERE username=? OR email=? OR mobile=?",
                (username, email, mobile),
            )
            existing = cur.fetchone()

            if existing:
                return render_template("register.html", error="User already exists!")

            cur.execute(
                """
                INSERT INTO users (username, email, mobile, password)
                VALUES (?, ?, ?, ?)
                """,
                (username, email, mobile, hashed_password),
            )
            db.commit()
            print(f"Registered new user: {username} ({mobile})")
        except sqlite3.IntegrityError as e:
            print("SQLite integrity error during registration:", e)
            return render_template("register.html", error="User already exists or invalid data.")
        except Exception as e:
            print("Unexpected error during registration:", e)
            return render_template("register.html", error="Registration failed due to server error.")
        finally:
            db.close()

        # Send welcome SMS to the newly registered mobile (if Twilio configured)
        try:
            sms_body = f"Welcome {username}! Your account has been created successfully."
            sent, info = send_alert_sms(mobile, sms_body)
            if sent:
                flash("Registration successful — welcome SMS sent.", "success")
            else:
                flash(f"Registration successful but SMS failed: {info}", "warning")
        except Exception as e:
            print("Error sending welcome SMS:", e)
            flash("Registration successful but SMS failed due to server error.", "warning")

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("dashboard.html", user=session["user"], auth_type=session.get("auth_type", "user"))


@app.route("/transaction", methods=["GET", "POST"])
def transaction():
    if not is_logged_in():
        return redirect(url_for("login"))

    if request.method == "POST":
        try:
            receiver_input = request.form.get("mobile", "").strip()
            receiver = normalize_phone(receiver_input)
            if not receiver:
                return render_template("transaction.html", error="Enter a valid recipient mobile number (e.g. +919629451234)")
            raw_amount = request.form["amount"].strip()

            try:
                amount = float(raw_amount)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                return render_template("transaction.html", error="Enter a valid amount greater than 0.")

            sender = session["user"]
            ip = request.remote_addr
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            db = get_db()
            try:
                cur = db.cursor()
                cur.execute("SELECT * FROM users WHERE mobile=?", (receiver,))
                rec_user_row = cur.fetchone()
                rec_user = dict(rec_user_row) if rec_user_row else None

                cur.execute("SELECT mobile FROM users WHERE username=?", (sender,))
                sender_mobile_row = cur.fetchone()
                sender_mobile = sender_mobile_row[0] if sender_mobile_row else None
                # Normalize sender_mobile (from DB) as well to compare safely
                sender_mobile = normalize_phone(sender_mobile) if sender_mobile else None
            finally:
                db.close()

            if sender_mobile and receiver == sender_mobile:
                return render_template("transaction.html", error="Cannot send to yourself!")

            # Default status is success. If receiver isn't registered, flag as fraud.
            status = "success"
            notice = None
            sms_body = None
            if not rec_user:
                status = "fraud"
                notice = "Receiver is not registered. Transaction flagged as fraud and alert sent to your mobile."
                create_admin_notification(
                    f"Fraud transaction blocked: sender={sender}, receiver={receiver}, amount={amount}, time={timestamp}",
                    source="web1_transaction",
                )
                if sender_mobile:
                    sms_body = (
                        f"ALERT: A transaction attempted to unregistered number {receiver} for amount {amount} "
                        f"from your account at {timestamp}. If this wasn't you, contact support immediately."
                    )

            # Before external API calls, we record the transaction.
            db = get_db()
            try:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO transactions (sender, receiver, amount, ip, timestamp, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (sender, receiver, amount, ip, timestamp, status),
                )
                tx_id = cur.lastrowid
                db.commit()
            finally:
                db.close()

            # If an alert SMS is sent, do it outside the DB lock.
            tw_sid = None
            tw_err = None
            if sms_body:
                success_val, info = send_alert_sms(sender_mobile, sms_body)
                if success_val:
                    tw_sid = info
                else:
                    tw_err = info

            # Update transaction row with Twilio info (if any)
            if tw_sid or tw_err:
                db = get_db()
                try:
                    cur = db.cursor()
                    cur.execute(
                        "UPDATE transactions SET twilio_sid=?, twilio_error=? WHERE id=?",
                        (tw_sid, tw_err, tx_id),
                    )
                    db.commit()
                finally:
                    db.close()
                
                if tw_err:
                    flash(f"SMS alert failed: {tw_err}", "danger")

            # Final success notification
            if notice:
                flash(notice, "warning")
            else:
                flash("Transaction Successful", "success")

        except Exception as e:
            # Safely log and handle any database or Twilio errors by redirecting back to logs
            print(f"Error during transaction: {str(e)}")
            flash(f"Transaction processing issue (it may have been logged): {str(e)}", "danger")
        
        # Always redirect to logs page even on internal failures
        return redirect(url_for("logs"))

    return render_template("transaction.html")


@app.route("/logs")
def logs():
    if not is_logged_in():
        return redirect(url_for("login"))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM transactions ORDER BY id DESC")
    rows = cur.fetchall()
    # convert to list of dicts for template access by column name
    data = [dict(r) for r in rows]
    db.close()

    return render_template("logs.html", data=data)


@app.route("/upload_statement", methods=["GET", "POST"])
def upload_statement():
    if not is_logged_in():
        return redirect(url_for("login"))

    if request.method == "POST":
        try:
            if 'file' not in request.files:
                flash('No file part', 'danger')
                return redirect(request.url)
            file = request.files['file']
            if file.filename == '':
                flash('No selected file', 'danger')
                return redirect(request.url)
            if file:
                filename = secure_filename(file.filename)
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(file_path)
                
                # AI Engine Configuration
                AI_TEMPERATURE = 0.0  # Rigid math mode, zero guessing

                def analyze_behavior(path, filename_clean):
                    try:
                        import pytesseract
                        from PIL import Image

                        file_ext = os.path.splitext(filename_clean)[1].lower()
                        text_data = ""
                        ocr_confidence = 0.0
                        ocr_low_confidence = False

                        # 1) OCR & Data Quality Check
                        if file_ext in [".png", ".jpg", ".jpeg", ".webp"]:
                            try:
                                image_obj = Image.open(path)
                                text_data = pytesseract.image_to_string(image_obj)
                                ocr_dict = pytesseract.image_to_data(image_obj, output_type=pytesseract.Output.DICT)
                                conf_values = [float(c) for c in ocr_dict.get("conf", []) if float(c) >= 0]
                                if conf_values:
                                    ocr_confidence = sum(conf_values) / len(conf_values)
                                ocr_low_confidence = (ocr_confidence < 50.0) or (len(text_data.strip()) < 100)
                            except Exception:
                                ocr_low_confidence = True
                        else:
                            try:
                                with open(path, "rb") as f:
                                    raw_bytes = f.read()
                                text_data = raw_bytes.decode("utf-8", errors="ignore")
                                ocr_low_confidence = len(text_data.strip()) < 100
                            except Exception:
                                ocr_low_confidence = True

                        text_lower = text_data.lower()
                        text_upper = text_data.upper()

                        # 2) Input Type Detection
                        detected_type = "Printed / Scanned Statement"
                        if file_ext == ".pdf":
                            detected_type = "Digital PDF Statement"
                        elif re.search(r"\b(battery|signal|wi-fi|status bar|carrier|airplane mode|am/pm)\b", text_lower):
                            detected_type = "Mobile Screenshot (Bank App)"
                        elif re.search(r"\b(passbook|folio|entry|page no|branch code)\b", text_lower):
                            detected_type = "Passbook Image"

                        # 3) Core Field Validation
                        bank_name_found = bool(re.search(r"\b(bank|hdfc|icici|sbi|axis|kotak|pnb|canara|yes bank|union bank)\b", text_lower))
                        holder_name_found = bool(re.search(r"(name|holder|customer|a/c\s*name)", text_lower))
                        account_number_found = bool(re.search(r"\b\d{10,16}\b", text_data))
                        ifsc_found = bool(re.search(r"\b[A-Z|1|I|L]{4}[0|O|5]{1}[A-Z0-9]{6}\b", text_upper))
                        period_found = bool(re.search(r"(period|from|to).{1,50}\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text_lower))
                        
                        # 4) Transaction & Balance Math (Critical)
                        def parse_money_val(v):
                            if not v: return 0.0
                            return float(re.sub(r"[^\d.]", "", v))

                        date_regex = r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
                        parsed_rows = []
                        prev_balance = None
                        mismatch_count = 0
                        
                        lines = text_data.splitlines()
                        for line in lines:
                            line = line.strip()
                            if not re.search(date_regex, line): continue
                            amounts = re.findall(r"\d{1,9}(?:,\d{3})*(?:\.\d{2})?", line)
                            if len(amounts) < 2: continue
                            
                            try:
                                curr_bal = parse_money_val(amounts[-1])
                                if prev_balance is not None:
                                    if len(amounts) >= 3:
                                        dr = parse_money_val(amounts[-3])
                                        cr = parse_money_val(amounts[-2])
                                    else:
                                        val = parse_money_val(amounts[-2])
                                        if round(curr_bal, 2) > round(prev_balance, 2):
                                            cr, dr = val, 0.0
                                        else:
                                            cr, dr = 0.0, val
                                    
                                    expected = round(prev_balance + cr - dr, 2)
                                    if abs(expected - round(curr_bal, 2)) > 0.05:
                                        mismatch_count += 1
                                
                                prev_balance = curr_bal
                                parsed_rows.append(True)
                            except Exception:
                                continue

                        # 5) Fraud Indicators (Strong Only)
                        fake_keywords = bool(re.search(r"\b(SAMPLE|DEMO|TEST COPY)\b", text_upper))
                        no_structure = len(parsed_rows) == 0
                        repetitive_numbers = bool(re.search(r"(\d)\1{5,}", text_data))
                        
                        # 6) Scoring System (0-100)
                        score = 0
                        if fake_keywords: score += 30
                        if mismatch_count > 3: score += 25
                        if no_structure: score += 20
                        if sum([not bank_name_found, not holder_name_found, not account_number_found]) >= 2: score += 15
                        if mismatch_count > 0 and mismatch_count <= 3: score += 10
                        
                        if account_number_found: score -= 10
                        if ifsc_found: score -= 10
                        if len(parsed_rows) > 5: score -= 15
                        if len(parsed_rows) > 0 and mismatch_count <= 1: score -= 20
                        if bank_name_found and holder_name_found: score -= 10
                        
                        score = max(0, min(100, score))
                        
                        # 7) Decision rules
                        if ocr_low_confidence:
                            status = "SUSPICIOUS"
                        elif score <= 35:
                            status = "GENUINE"
                        elif score <= 65:
                            status = "SUSPICIOUS"
                        else:
                            status = "FRAUD"
                            
                        if status == "FRAUD" and mismatch_count <= 3 and not fake_keywords and not repetitive_numbers:
                            status = "SUSPICIOUS"

                        reasons = []
                        if fake_keywords: reasons.append("Fraud pattern: Fraud keywords (SAMPLE/DEMO) detected.")
                        if mismatch_count > 3: reasons.append(f"Fraud pattern: Critical balance math failure ({mismatch_count} mismatches).")
                        if mismatch_count > 0 and mismatch_count <= 3: reasons.append(f"Data issue: Detected {mismatch_count} minor balance discrepancies (possible extraction error).")
                        if no_structure: reasons.append("Data issue: No valid transaction table structure found.")
                        if repetitive_numbers: reasons.append("Fraud pattern: Manipulated numeric patterns detected.")
                        if not ifsc_found: reasons.append("Data issue: IFSC code missing or invalid format.")
                        if not account_number_found: reasons.append("Data issue: Account number could not be verified.")
                        if ocr_low_confidence: reasons.append("Data issue: OCR extraction quality is low/incomplete.")
                        
                        if score <= 35 and not reasons:
                            reasons.append("Genuine pattern: All core fields and mathematical validations passed.")
                        
                        # Simplified 8-point report format
                        report = f"""Analysis Complete for: {filename_clean}

STATUS: {status}

REASON:
{chr(10).join(['- ' + r for r in reasons[:5]]) if reasons else '- Standard verification complete.'}

FRAUD PROBABILITY SCORE: {score}%

CONCLUSION:
- {status.capitalize()} statement confirmed with {score}% risk factor."""

                        return status, detected_type, mismatch_count, score, reasons[:5], report, status.capitalize()


                    except Exception as e:
                        return "SUSPICIOUS", "Unknown", 0, 50, [f"Error: {str(e)}"], "Analysis failed due to error", "Manual verification required"


                filename_clean = filename.lower()
                status_type, det_type, mismatches, f_score, reason_points, report_str, conclusion = analyze_behavior(file_path, filename_clean)
                
                is_fraud = (status_type == "FRAUD")
                result = status_type.lower()
                status_line = f"STATUS: {status_type}"
                
                display_reason = "<br>".join([f"- {point}" for point in reason_points])

                final_message = f"{status_line} | Type: {det_type} | Errors: {mismatches}"
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                username = session["user"]
                
                db = get_db()
                try:
                    cur = db.cursor()
                    
                    if is_fraud:
                        cur.execute(
                            "INSERT INTO notifications (username, message, timestamp) VALUES (?, ?, ?)",
                            (username, final_message, timestamp)
                        )
                        db.commit()
                        flash(final_message, "danger")
                    elif status_type == "SUSPICIOUS":
                        flash(final_message, "warning")
                    else:
                        flash(final_message, "success")
                finally:
                    db.close()

                if is_fraud:
                    create_admin_notification(
                        f"Fraud statement detected for user={username} file={filename} at {timestamp}: {final_message}",
                        source="web1_statement",
                    )
                
                # Pass everything to the exact UI card
                return render_template("upload_statement.html", 
                                     result=result, 
                                     filename=filename, 
                                     risk_score=f_score,
                                     genuine_score=(100 - f_score),
                                     audit_status=status_line,
                                     detected_type=det_type,
                                     mismatch_count=mismatches,
                                     reason=display_reason,
                                     conclusion=conclusion,
                                     strict_report=report_str)

        except Exception as e:
            print(f"Error in upload_statement: {str(e)}")
            flash(f"Error processing statement: {str(e)}", "danger")
            return redirect(request.url)

    return render_template("upload_statement.html")


@app.context_processor
def inject_notifications():
    if is_logged_in():
        return {'notif_count': get_notification_count()}
    return {'notif_count': 0}


@app.route("/api/fraud_stats")
def fraud_stats():
    if not is_logged_in():
        return jsonify({"error": "Unauthorized"}), 401
        
    db = get_db()
    try:
        cur = db.cursor()
        
        # 1. Genuine vs Fraud Transactions
        cur.execute("SELECT status, COUNT(*) FROM transactions GROUP BY status")
        tx_stats = {row[0]: row[1] for row in cur.fetchall()}
        
        # 2. Top Fraud Targets (Receivers)
        cur.execute(
            """
            SELECT receiver, COUNT(*) as count 
            FROM transactions 
            WHERE status='fraud' 
            GROUP BY receiver 
            ORDER BY count DESC 
            LIMIT 5
            """
        )
        targets = [{"name": row[0], "count": row[1]} for row in cur.fetchall()]
        
        # 3. Fraud Alerts from Statements (by User)
        cur.execute(
            """
            SELECT username, COUNT(*) as count 
            FROM notifications 
            WHERE message LIKE '%ALERT%' OR message LIKE '%FRAUD DETECTED%' 
            GROUP BY username 
            ORDER BY count DESC 
            LIMIT 5
            """
        )
        alerts = [{"name": row[0], "count": row[1]} for row in cur.fetchall()]
        
        return jsonify({
            "tx_stats": tx_stats,
            "top_targets": targets,
            "top_alerts": alerts
        })
    finally:
        db.close()


@app.route("/api/notifications/unread")
def unread_count():
    if not is_logged_in():
        return jsonify({"count": 0})
    count = get_notification_count()
    return jsonify({"count": count})


@app.route("/api/notifications/list")
def list_notifications():
    if not is_logged_in():
        return jsonify({"notifications": []})
    rows = get_notification_rows()
    return jsonify({
        "notifications": [{"message": r[0], "time": r[1]} for r in rows]
    })


@app.route("/api/notifications/clear", methods=["POST"])
def clear_notifications():
    if not is_logged_in():
        return jsonify({"success": False})

    db = get_db()
    try:
        cur = db.cursor()
        if is_admin_session():
            cur.execute("UPDATE admin_notifications SET is_read=1 WHERE is_read=0")
        else:
            cur.execute("UPDATE notifications SET is_read=1 WHERE username=?", (session["user"],))
        db.commit()
        return jsonify({"success": True})
    finally:
        db.close()


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
