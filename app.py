from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response
from datetime import datetime, date, timedelta
import io
import hashlib
import os
import sqlite3
from functools import wraps

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    from twilio.rest import Client
except ImportError:
    Client = None

app = Flask(__name__)
app.secret_key = "hms_secret_key_2024"

DB = "hms.db"
CANCELLATION_WINDOW_HOURS = 2
DEFAULT_SLOT_MINUTES = 30
DEFAULT_WORK_START = "09:00"
DEFAULT_WORK_END = "17:00"
DEFAULT_LUNCH_START = "13:00"
DEFAULT_LUNCH_END = "14:00"
DOCTOR_DEMO_PASSWORD = "doctor@123"
RECEPTIONIST_DEMO_PASSWORD = "recep123"
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
SPECIALIZATIONS = [
    "General Medicine",
    "Cardiology",
    "Dermatology",
    "Diabetology",
    "ENT",
    "Endocrinology",
    "Gastroenterology",
    "Gynecology",
    "Infectious Disease",
    "Neurology",
    "Orthopedics",
    "Pediatrics",
    "Psychiatry",
    "Pulmonology",
]
ACTIVE_APPOINTMENT_STATUSES = ("scheduled", "checked_in", "in_consultation")
PRESCRIPTION_TEMPLATES = {
    "Viral Fever": {
        "diagnosis": "Viral fever",
        "medicines": "Tab. Paracetamol 500mg - 1 tablet TDS for 5 days\nTab. Cetirizine 10mg - 1 tablet OD at night\nORS - Sip frequently for 2 days",
    },
    "Diabetes": {
        "diagnosis": "Type 2 Diabetes Mellitus",
        "medicines": "Tab. Metformin 500mg - 1 tablet BD after food\nTab. Glimepiride 1mg - 1 tablet before breakfast\nBlood sugar monitoring advice",
    },
    "Thyroid": {
        "diagnosis": "Hypothyroidism",
        "medicines": "Tab. Thyroxine 50mcg - 1 tablet OD before breakfast\nTSH review after 6 weeks",
    },
    "Malaria": {
        "diagnosis": "Malaria under treatment",
        "medicines": "Antimalarial as per protocol\nTab. Paracetamol 500mg - 1 tablet SOS for fever\nHydration and CBC review",
    },
    "Throat Pain": {
        "diagnosis": "Acute throat pain / pharyngitis",
        "medicines": "Tab. Paracetamol 500mg - 1 tablet TDS\nWarm saline gargles - 3 times daily\nLozenges - 1 as needed",
    },
    "Hypertension": {
        "diagnosis": "Hypertension follow-up",
        "medicines": "Tab. Amlodipine 5mg - 1 tablet OD\nTab. Telmisartan 40mg - 1 tablet OD\nLow salt diet advice",
    },
    "Migraine": {
        "diagnosis": "Migraine headache",
        "medicines": "Tab. Naproxen - 1 tablet SOS after food\nAdequate hydration advice\nAvoid known migraine triggers",
    },
    "Acidity": {
        "diagnosis": "Acidity / gastritis",
        "medicines": "Tab. Pantoprazole 40mg - 1 tablet OD before breakfast\nSyrup antacid - 10ml TDS after meals\nAvoid spicy food for 5 days",
    },
    "Cold and Cough": {
        "diagnosis": "Common cold with cough",
        "medicines": "Tab. Cetirizine 10mg - 1 tablet OD at night\nCough syrup - 10ml TDS\nSteam inhalation twice daily",
    },
    "Allergy": {
        "diagnosis": "Allergic rhinitis / allergy",
        "medicines": "Tab. Levocetirizine 5mg - 1 tablet OD at night\nNasal saline spray - 2 puffs TDS\nAvoid dust exposure",
    },
    "Back Pain": {
        "diagnosis": "Mechanical low back pain",
        "medicines": "Tab. Paracetamol 650mg - 1 tablet TDS after food\nTopical pain gel - apply locally twice daily\nBack stretching advice",
    },
    "Skin Rash": {
        "diagnosis": "Simple skin rash / dermatitis",
        "medicines": "Antihistamine tablet - 1 at night\nTopical soothing lotion - apply twice daily\nKeep affected area clean and dry",
    },
    "Anemia": {
        "diagnosis": "Iron deficiency anemia",
        "medicines": "Iron and folic acid tablet - 1 tablet OD after meals\nDiet advice with leafy vegetables and dates\nCBC review after 4 weeks",
    },
    "Asthma Follow-up": {
        "diagnosis": "Bronchial asthma follow-up",
        "medicines": "Inhaler as previously prescribed\nSteam inhalation if needed\nAvoid smoke and dust exposure",
    },
    "Constipation": {
        "diagnosis": "Constipation",
        "medicines": "Fiber supplement - 1 serving OD\nIncrease water intake\nMild laxative at bedtime if needed",
    },
}


def hash_pw(password):
    return hashlib.sha256(password.encode()).hexdigest()


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, definition):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL,
            specialization TEXT,
            phone TEXT,
            availability_status TEXT DEFAULT 'available',
            availability_note TEXT,
            availability_updated_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS patient_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            gender TEXT NOT NULL,
            phone TEXT UNIQUE,
            address TEXT,
            blood_group TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_visit_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER,
            name TEXT NOT NULL,
            age INTEGER NOT NULL,
            gender TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            blood_group TEXT,
            admitted_by INTEGER,
            admitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'waiting',
            assigned_doctor INTEGER,
            required_specialization TEXT,
            visit_reason TEXT,
            visit_number INTEGER DEFAULT 1,
            FOREIGN KEY(admitted_by) REFERENCES users(id),
            FOREIGN KEY(assigned_doctor) REFERENCES users(id),
            FOREIGN KEY(profile_id) REFERENCES patient_profiles(id)
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            appointment_date DATE NOT NULL,
            appointment_time TEXT NOT NULL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'scheduled',
            payment_status TEXT DEFAULT 'pending',
            payment_mode TEXT,
            transaction_id TEXT,
            fee_amount REAL DEFAULT 0,
            cancellation_deadline TEXT,
            cancelled_at TEXT,
            cancelled_by INTEGER,
            refund_status TEXT DEFAULT 'not_applicable',
            reschedule_reason TEXT,
            patient_alert_message TEXT,
            patient_alert_sent_at TEXT,
            notification_phone TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id),
            FOREIGN KEY(doctor_id) REFERENCES users(id),
            FOREIGN KEY(cancelled_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS prescriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            diagnosis TEXT NOT NULL,
            medicines TEXT NOT NULL,
            instructions TEXT,
            follow_up_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(patient_id) REFERENCES patients(id),
            FOREIGN KEY(doctor_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS doctor_schedules (
            doctor_id INTEGER PRIMARY KEY,
            work_start TEXT NOT NULL,
            work_end TEXT NOT NULL,
            lunch_start TEXT NOT NULL,
            lunch_end TEXT NOT NULL,
            slot_minutes INTEGER NOT NULL DEFAULT 30,
            FOREIGN KEY(doctor_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS doctor_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            block_date DATE NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            block_type TEXT NOT NULL DEFAULT 'busy',
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(doctor_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_role TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read INTEGER DEFAULT 0
        );
        """
    )

    for column, definition in [
        ("specialization", "TEXT"),
        ("phone", "TEXT"),
        ("availability_status", "TEXT DEFAULT 'available'"),
        ("availability_note", "TEXT"),
        ("availability_updated_at", "TIMESTAMP"),
    ]:
        ensure_column(conn, "users", column, definition)

    for column, definition in [
        ("profile_id", "INTEGER"),
        ("required_specialization", "TEXT"),
        ("visit_reason", "TEXT"),
        ("visit_number", "INTEGER DEFAULT 1"),
    ]:
        ensure_column(conn, "patients", column, definition)

    for column, definition in [
        ("status", "TEXT DEFAULT 'scheduled'"),
        ("payment_status", "TEXT DEFAULT 'pending'"),
        ("payment_mode", "TEXT"),
        ("transaction_id", "TEXT"),
        ("fee_amount", "REAL DEFAULT 0"),
        ("cancellation_deadline", "TEXT"),
        ("cancelled_at", "TEXT"),
        ("cancelled_by", "INTEGER"),
        ("refund_status", "TEXT DEFAULT 'not_applicable'"),
        ("reschedule_reason", "TEXT"),
        ("patient_alert_message", "TEXT"),
        ("patient_alert_sent_at", "TEXT"),
        ("notification_phone", "TEXT"),
    ]:
        ensure_column(conn, "appointments", column, definition)

    users = [
        ("receptionist", hash_pw(RECEPTIONIST_DEMO_PASSWORD), "receptionist", "Sarah Johnson", None, "9876500001"),
        ("doctor1", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Arun Kumar", "General Medicine", "9876500011"),
        ("doctor2", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Priya Nair", "Diabetology", "9876500012"),
        ("doctor3", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Meera Shah", "Cardiology", "9876500013"),
        ("doctor4", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Rohan Iyer", "ENT", "9876500014"),
        ("doctor5", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Kavya Menon", "Dermatology", "9876500015"),
        ("doctor6", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Siddharth Rao", "Orthopedics", "9876500016"),
        ("doctor7", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Nisha Thomas", "Pediatrics", "9876500017"),
        ("doctor8", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Farhan Ali", "Pulmonology", "9876500018"),
        ("doctor9", hash_pw(DOCTOR_DEMO_PASSWORD), "doctor", "Dr. Ananya Bose", "Endocrinology", "9876500019"),
    ]
    for user in users:
        try:
            c.execute(
                """
                INSERT INTO users (username, password, role, full_name, specialization, phone)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                user,
            )
        except sqlite3.IntegrityError:
            pass

    doctor_specs = {
        "doctor1": "General Medicine",
        "doctor2": "Diabetology",
        "doctor3": "Cardiology",
        "doctor4": "ENT",
        "doctor5": "Dermatology",
        "doctor6": "Orthopedics",
        "doctor7": "Pediatrics",
        "doctor8": "Pulmonology",
        "doctor9": "Endocrinology",
    }
    for username, specialization in doctor_specs.items():
        c.execute(
            "UPDATE users SET specialization=COALESCE(NULLIF(specialization, ''), ?), availability_status=COALESCE(NULLIF(availability_status, ''), 'available') WHERE username=?",
            (specialization, username),
        )
    c.execute("UPDATE users SET password=? WHERE role='doctor'", (hash_pw(DOCTOR_DEMO_PASSWORD),))
    c.execute("UPDATE users SET password=? WHERE username='receptionist'", (hash_pw(RECEPTIONIST_DEMO_PASSWORD),))

    doctor_rows = c.execute("SELECT id FROM users WHERE role='doctor'").fetchall()
    for doctor in doctor_rows:
        c.execute(
            """
            INSERT OR IGNORE INTO doctor_schedules (doctor_id, work_start, work_end, lunch_start, lunch_end, slot_minutes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (doctor["id"], DEFAULT_WORK_START, DEFAULT_WORK_END, DEFAULT_LUNCH_START, DEFAULT_LUNCH_END, DEFAULT_SLOT_MINUTES),
        )

    c.execute("UPDATE patients SET visit_number=COALESCE(visit_number, 1)")
    c.execute("UPDATE users SET availability_status=COALESCE(NULLIF(availability_status, ''), 'available') WHERE role='doctor'")
    c.execute("UPDATE appointments SET status=COALESCE(NULLIF(status, ''), 'scheduled')")
    c.execute("UPDATE appointments SET payment_status=COALESCE(NULLIF(payment_status, ''), 'pending')")
    conn.commit()
    conn.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get("role") not in roles:
                flash("Access denied.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)

        return decorated

    return decorator


def format_time_label(value):
    hour, minute = [int(part) for part in value.split(":")]
    display_hour = hour if hour <= 12 else hour - 12
    period = "AM" if hour < 12 else "PM"
    return f"{display_hour:02d}:{minute:02d} {period}"


def minutes_since_midnight(value):
    hour, minute = [int(part) for part in value.split(":")]
    return hour * 60 + minute


def get_slot_options(start=DEFAULT_WORK_START, end=DEFAULT_WORK_END, step=DEFAULT_SLOT_MINUTES):
    options = []
    current = minutes_since_midnight(start)
    stop = minutes_since_midnight(end)
    while current < stop:
        hour = current // 60
        minute = current % 60
        value = f"{hour:02d}:{minute:02d}"
        options.append({"value": value, "label": format_time_label(value)})
        current += step
    return options


def appointment_datetime(appointment_date, appointment_time):
    return datetime.strptime(f"{appointment_date} {appointment_time}", "%Y-%m-%d %H:%M")


def build_cancellation_deadline(appointment_date, appointment_time):
    deadline = appointment_datetime(appointment_date, appointment_time) - timedelta(hours=CANCELLATION_WINDOW_HOURS)
    return deadline.strftime("%Y-%m-%d %H:%M:%S")


def format_alert_message(patient_name, doctor_name, new_date, new_time, reason):
    reason_text = f" Reason: {reason}." if reason else ""
    return (
        f"Dear {patient_name}, your appointment with {doctor_name} has been rescheduled to "
        f"{new_date} at {new_time}.{reason_text} Please visit the hospital at the updated time."
    )


def build_booking_message(patient_name, doctor_name, appointment_date, appointment_time):
    return (
        f"Dear {patient_name}, your appointment with {doctor_name} is confirmed for "
        f"{appointment_date} at {appointment_time}. Please arrive a few minutes early."
    )


def build_cancellation_message(patient_name, doctor_name, appointment_date, appointment_time, refund_status):
    refund_text = "Refund is available." if refund_status == "eligible" else "Refund is not available."
    return (
        f"Dear {patient_name}, your appointment with {doctor_name} on "
        f"{appointment_date} at {appointment_time} has been cancelled. {refund_text}"
    )


def sms_ready():
    return bool(Client and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


def send_sms_notification(to_number, message):
    if not to_number:
        return False, "Patient phone number is missing."
    if not sms_ready():
        return False, "Twilio SMS is not configured yet."
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=message,
            from_=TWILIO_FROM_NUMBER,
            to=to_number,
        )
        return True, "SMS sent."
    except Exception as exc:
        return False, f"SMS failed: {exc}"


def create_notification(conn, target_role, title, message):
    conn.execute(
        """
        INSERT INTO notifications (target_role, title, message)
        VALUES (?, ?, ?)
        """,
        (target_role, title, message),
    )


def get_notifications(conn, target_role, limit=10, unread_only=False):
    query = """
        SELECT *
        FROM notifications
        WHERE target_role=?
    """
    params = [target_role]
    if unread_only:
        query += " AND is_read=0"
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()


def active_status_placeholders():
    return ",".join("?" for _ in ACTIVE_APPOINTMENT_STATUSES)


def get_doctors(conn, specialization=None):
    query = """
        SELECT u.id, u.username, u.full_name, u.specialization, u.phone,
               u.availability_status, u.availability_note, u.availability_updated_at,
               ds.work_start, ds.work_end, ds.lunch_start, ds.lunch_end, ds.slot_minutes
        FROM users u
        LEFT JOIN doctor_schedules ds ON ds.doctor_id=u.id
        WHERE u.role='doctor'
    """
    params = []
    if specialization:
        query += " AND u.specialization=?"
        params.append(specialization)
    query += " ORDER BY u.full_name"
    return conn.execute(query, params).fetchall()


def find_available_doctor(conn, specialization):
    if not specialization:
        return None
    return conn.execute(
        """
        SELECT u.id, u.full_name, u.specialization
        FROM users u
        WHERE u.role='doctor'
          AND u.specialization=?
          AND COALESCE(u.availability_status, 'available')='available'
        ORDER BY full_name
        LIMIT 1
        """,
        (specialization,),
    ).fetchone()

def get_doctor_schedule(conn, doctor_id):
    schedule = conn.execute(
        """
        SELECT work_start, work_end, lunch_start, lunch_end, slot_minutes
        FROM doctor_schedules
        WHERE doctor_id=?
        """,
        (doctor_id,),
    ).fetchone()
    if schedule:
        return schedule
    return {
        "work_start": DEFAULT_WORK_START,
        "work_end": DEFAULT_WORK_END,
        "lunch_start": DEFAULT_LUNCH_START,
        "lunch_end": DEFAULT_LUNCH_END,
        "slot_minutes": DEFAULT_SLOT_MINUTES,
    }


def slot_within_schedule(schedule, appointment_time):
    current = minutes_since_midnight(appointment_time)
    work_start = minutes_since_midnight(schedule["work_start"])
    work_end = minutes_since_midnight(schedule["work_end"])
    lunch_start = minutes_since_midnight(schedule["lunch_start"])
    lunch_end = minutes_since_midnight(schedule["lunch_end"])
    return work_start <= current < work_end and not (lunch_start <= current <= lunch_end)


def slot_block_reason(conn, doctor_id, appointment_date, appointment_time):
    block = conn.execute(
        """
        SELECT block_type, reason, start_time, end_time
        FROM doctor_blocks
        WHERE doctor_id=?
          AND block_date=?
          AND start_time <= ?
          AND end_time > ?
        ORDER BY start_time
        LIMIT 1
        """,
        (doctor_id, appointment_date, appointment_time, appointment_time),
    ).fetchone()
    return block


def validate_slot(conn, doctor_id, appointment_date, appointment_time, ignore_appointment_id=None):
    schedule = get_doctor_schedule(conn, doctor_id)
    if not slot_within_schedule(schedule, appointment_time):
        current = minutes_since_midnight(appointment_time)
        lunch_start = minutes_since_midnight(schedule["lunch_start"])
        lunch_end = minutes_since_midnight(schedule["lunch_end"])
        if lunch_start <= current < lunch_end:
            return {"kind": "lunch", "message": "This slot falls in the doctor's lunch break."}
        return {"kind": "outside_hours", "message": "This slot is outside the doctor's working hours."}

    block = slot_block_reason(conn, doctor_id, appointment_date, appointment_time)
    if block:
        return {
            "kind": "blocked",
            "message": f"Doctor is marked {block['block_type']} from {block['start_time']} to {block['end_time']}. {block['reason'] or ''}".strip(),
        }

    params = [doctor_id, appointment_date, appointment_time, *ACTIVE_APPOINTMENT_STATUSES]
    query = f"""
        SELECT id
        FROM appointments
        WHERE doctor_id=?
          AND appointment_date=?
          AND appointment_time=?
          AND status IN ({active_status_placeholders()})
    """
    if ignore_appointment_id:
        query += " AND id != ?"
        params.append(ignore_appointment_id)
    clash = conn.execute(query, params).fetchone()
    if clash:
        return {"kind": "booked", "message": "This appointment slot is already booked."}
    return None


def get_or_create_profile(conn, name, gender, phone, address, blood_group):
    profile = None
    if phone:
        profile = conn.execute("SELECT * FROM patient_profiles WHERE phone=?", (phone,)).fetchone()
    if not profile:
        profile = conn.execute(
            """
            SELECT *
            FROM patient_profiles
            WHERE name=? AND gender=?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (name, gender),
        ).fetchone()
    if profile:
        conn.execute(
            """
            UPDATE patient_profiles
            SET name=?, gender=?, phone=?, address=?, blood_group=?, updated_at=CURRENT_TIMESTAMP, last_visit_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (name, gender, phone or profile["phone"], address, blood_group, profile["id"]),
        )
        return profile["id"], True

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO patient_profiles (name, gender, phone, address, blood_group, last_visit_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (name, gender, phone or None, address, blood_group),
    )
    return cursor.lastrowid, False


def get_profile_visits(conn, profile_id):
    return conn.execute(
        """
        SELECT p.id, p.age, p.visit_reason, p.status, p.admitted_at, u.full_name AS doctor_name
        FROM patients p
        LEFT JOIN users u ON p.assigned_doctor=u.id
        WHERE p.profile_id=?
        ORDER BY p.admitted_at DESC
        """,
        (profile_id,),
    ).fetchall()


def build_doctor_day_calendar(conn, doctor, target_date):
    schedule = get_doctor_schedule(conn, doctor["id"])
    appointments = conn.execute(
        f"""
        SELECT appointment_time, status, patient_id
        FROM appointments
        WHERE doctor_id=?
          AND appointment_date=?
          AND status IN ({active_status_placeholders()})
        """,
        (doctor["id"], target_date, *ACTIVE_APPOINTMENT_STATUSES),
    ).fetchall()
    booked = {row["appointment_time"]: row for row in appointments}
    blocks = conn.execute(
        """
        SELECT start_time, end_time, block_type, reason
        FROM doctor_blocks
        WHERE doctor_id=? AND block_date=?
        ORDER BY start_time
        """,
        (doctor["id"], target_date),
    ).fetchall()
    slots = []
    for slot in get_slot_options(schedule["work_start"], schedule["work_end"], schedule["slot_minutes"]):
        current = minutes_since_midnight(slot["value"])
        lunch_start = minutes_since_midnight(schedule["lunch_start"])
        lunch_end = minutes_since_midnight(schedule["lunch_end"])
        state = "available"
        note = ""
        if lunch_start <= current <= lunch_end:
            state = "lunch"
            note = f"Lunch break until {format_time_label(schedule['lunch_end'])}"
        for block in blocks:
            if block["start_time"] <= slot["value"] < block["end_time"]:
                state = "busy"
                note = block["reason"] or "Doctor unavailable"
                break
        if slot["value"] in booked:
            state = "booked"
            note = booked[slot["value"]]["status"].replace("_", " ")
        slots.append({"time": slot["value"], "label": slot["label"], "state": state, "note": note})
    return {
        "doctor_id": doctor["id"],
        "doctor_name": doctor["full_name"],
        "specialization": doctor["specialization"],
        "work_start": schedule["work_start"],
        "work_end": schedule["work_end"],
        "lunch_start": schedule["lunch_start"],
        "lunch_end": schedule["lunch_end"],
        "slot_minutes": schedule["slot_minutes"],
        "slots": slots,
    }


@app.context_processor
def inject_helpers():
    unread_notifications = 0
    if session.get("role") == "receptionist":
        try:
            conn = get_db()
            unread_notifications = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE target_role='receptionist' AND is_read=0"
            ).fetchone()[0]
            conn.close()
        except Exception:
            unread_notifications = 0
    return {
        "slot_options": get_slot_options(),
        "specializations": SPECIALIZATIONS,
        "cancellation_window_hours": CANCELLATION_WINDOW_HOURS,
        "prescription_templates": PRESCRIPTION_TEMPLATES,
        "format_time_label": format_time_label,
        "unread_notifications": unread_notifications,
    }


@app.route("/", methods=["GET", "POST"])
def login():
    conn = get_db()
    doctors = get_doctors(conn)
    if "user_id" in session:
        conn.close()
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (username, hash_pw(password)),
        ).fetchone()
        conn.close()
        if user:
            if user["role"] == "admin":
                flash("Admin access has been removed from this application.", "danger")
                return render_template("login.html", doctors=doctors, doctor_demo_password=DOCTOR_DEMO_PASSWORD)
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            session["specialization"] = user["specialization"]
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
        return render_template("login.html", doctors=doctors, doctor_demo_password=DOCTOR_DEMO_PASSWORD)
    conn.close()
    return render_template("login.html", doctors=doctors, doctor_demo_password=DOCTOR_DEMO_PASSWORD)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    today = date.today().isoformat()
    total_patients = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    total_profiles = conn.execute("SELECT COUNT(*) FROM patient_profiles").fetchone()[0]
    today_admitted = conn.execute("SELECT COUNT(*) FROM patients WHERE DATE(admitted_at)=?", (today,)).fetchone()[0]
    waiting_patients = conn.execute("SELECT COUNT(*) FROM patients WHERE status='waiting'").fetchone()[0]
    in_consultation = conn.execute("SELECT COUNT(*) FROM patients WHERE status='in_consultation'").fetchone()[0]
    prescribed = conn.execute("SELECT COUNT(*) FROM patients WHERE status='prescribed'").fetchone()[0]
    discharged = conn.execute("SELECT COUNT(*) FROM patients WHERE status='discharged'").fetchone()[0]
    total_doctors = conn.execute("SELECT COUNT(*) FROM users WHERE role='doctor'").fetchone()[0]
    today_appts = conn.execute(
        """
        SELECT COUNT(*)
        FROM appointments
        WHERE appointment_date=?
          AND status IN (?, ?, ?)
        """,
        (today, *ACTIVE_APPOINTMENT_STATUSES),
    ).fetchone()[0]
    busy_doctors = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role='doctor' AND availability_status='busy'"
    ).fetchone()[0]
    if session["role"] == "doctor":
        recent_patients = conn.execute(
            """
            SELECT p.*, u.full_name AS doctor_name, u.specialization AS doctor_specialization
            FROM patients p
            LEFT JOIN users u ON p.assigned_doctor = u.id
            WHERE p.assigned_doctor=?
              AND p.status IN ('waiting', 'in_consultation')
            ORDER BY p.admitted_at DESC
            LIMIT 5
            """,
            (session["user_id"],),
        ).fetchall()
    else:
        recent_patients = conn.execute(
            """
            SELECT p.*, u.full_name AS doctor_name, u.specialization AS doctor_specialization
            FROM patients p
            LEFT JOIN users u ON p.assigned_doctor = u.id
            ORDER BY p.admitted_at DESC
            LIMIT 5
            """
        ).fetchall()
    recent_notifications = get_notifications(conn, "receptionist", limit=5, unread_only=True) if session["role"] == "receptionist" else []
    conn.close()
    stats = {
        "total": total_patients,
        "profiles": total_profiles,
        "today": today_admitted,
        "waiting": waiting_patients,
        "consultation": in_consultation,
        "prescribed": prescribed,
        "discharged": discharged,
        "doctors": total_doctors,
        "appts": today_appts,
        "busy_doctors": busy_doctors,
    }
    return render_template("dashboard.html", stats=stats, recent=recent_patients, recent_notifications=recent_notifications)


@app.route("/patients")
@login_required
def patients():
    conn = get_db()
    q = request.args.get("q", "")
    status_filter = request.args.get("status", "")
    query = """
        SELECT p.*, u.full_name AS doctor_name, u.specialization AS doctor_specialization,
               pp.id AS profile_id,
               (SELECT COUNT(*) FROM patients pv WHERE pv.profile_id=p.profile_id) AS total_visits
        FROM patients p
        LEFT JOIN users u ON p.assigned_doctor=u.id
        LEFT JOIN patient_profiles pp ON p.profile_id=pp.id
        WHERE (p.name LIKE ? OR p.phone LIKE ?)
    """
    params = [f"%{q}%", f"%{q}%"]
    if status_filter:
        query += " AND p.status=?"
        params.append(status_filter)
    query += " ORDER BY p.admitted_at DESC"
    all_patients = conn.execute(query, params).fetchall()
    conn.close()
    return render_template("patients.html", patients=all_patients, q=q, status_filter=status_filter)


@app.route("/patients/admit", methods=["GET", "POST"])
@login_required
@role_required("receptionist")
def admit_patient():
    conn = get_db()
    doctors = get_doctors(conn)
    today = request.args.get("date") or date.today().isoformat()
    schedule_board = [build_doctor_day_calendar(conn, doctor, today) for doctor in doctors]
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        name = request.form["name"].strip()
        age = request.form["age"]
        gender = request.form["gender"]
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        blood_group = request.form.get("blood_group", "")
        visit_reason = request.form.get("visit_reason", "").strip()
        required_specialization = request.form.get("required_specialization", "").strip()
        doctor_id = request.form.get("doctor_id") or None
        appointment_date = request.form.get("appt_date", "")
        appointment_time = request.form.get("appt_time", "")
        notes = request.form.get("notes", "").strip()
        fee_amount = float(request.form.get("fee_amount") or 0)
        payment_mode = request.form.get("payment_mode", "")
        transaction_id = request.form.get("transaction_id", "").strip()

        if not required_specialization and doctor_id:
            selected = next((d for d in doctors if str(d["id"]) == str(doctor_id)), None)
            required_specialization = selected["specialization"] if selected else ""

        if required_specialization and not doctor_id:
            doctor = find_available_doctor(conn, required_specialization)
            if doctor:
                doctor_id = doctor["id"]
            else:
                flash("No available doctor found for the selected specialization.", "danger")
                conn.close()
                return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)

        if not doctor_id:
            flash("Please assign a doctor for the appointment.", "danger")
            conn.close()
            return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)

        doctor = conn.execute(
            """
            SELECT id, full_name, specialization, availability_status
            FROM users
            WHERE id=? AND role='doctor'
            """,
            (doctor_id,),
        ).fetchone()
        if not doctor:
            flash("Selected doctor was not found.", "danger")
            conn.close()
            return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)

        if required_specialization and doctor["specialization"] != required_specialization:
            flash("Selected doctor does not match the requested specialization.", "danger")
            conn.close()
            return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)

        if not appointment_date or not appointment_time:
            flash("Please choose the appointment date and time.", "danger")
            conn.close()
            return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)

        if payment_mode not in ("online", "cash"):
            flash("Please mark whether the appointment fee was paid online or by cash.", "danger")
            conn.close()
            return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)
        if payment_mode == "online" and not transaction_id:
            flash("Transaction ID is required for online payments.", "danger")
            conn.close()
            return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)

        slot_issue = validate_slot(conn, doctor_id, appointment_date, appointment_time)
        if slot_issue:
            flash(slot_issue["message"], "danger")
            conn.close()
            return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)

        cancellation_deadline = build_cancellation_deadline(appointment_date, appointment_time)
        profile_id, existing_profile = get_or_create_profile(conn, name, gender, phone, address, blood_group)
        visit_number = conn.execute("SELECT COUNT(*) FROM patients WHERE profile_id=?", (profile_id,)).fetchone()[0] + 1

        c = conn.cursor()
        c.execute(
            """
            INSERT INTO patients
            (profile_id, name, age, gender, phone, address, blood_group, admitted_by, assigned_doctor, required_specialization, visit_reason, visit_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                name,
                age,
                gender,
                phone,
                address,
                blood_group,
                session["user_id"],
                doctor_id,
                required_specialization,
                visit_reason,
                visit_number,
            ),
        )
        patient_id = c.lastrowid
        c.execute(
            """
            INSERT INTO appointments
            (
                patient_id, doctor_id, appointment_date, appointment_time, notes, status,
                payment_status, payment_mode, transaction_id, fee_amount, cancellation_deadline, notification_phone
            )
            VALUES (?, ?, ?, ?, ?, 'scheduled', 'paid', ?, ?, ?, ?, ?)
            """,
            (
                patient_id,
                doctor_id,
                appointment_date,
                appointment_time,
                notes,
                payment_mode,
                transaction_id or None,
                fee_amount,
                cancellation_deadline,
                phone,
            ),
        )
        appointment_id = c.lastrowid
        booking_message = build_booking_message(name, doctor["full_name"], appointment_date, appointment_time)
        sent, sms_note = send_sms_notification(phone, booking_message)
        c.execute(
            """
            UPDATE appointments
            SET patient_alert_message=?, patient_alert_sent_at=?
            WHERE id=?
            """,
            (
                booking_message if sent else f"{booking_message} [{sms_note}]",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S") if sent else None,
                appointment_id,
            ),
        )
        conn.commit()
        conn.close()
        if existing_profile:
            flash(f"Returning patient found. Appointment added to the existing profile for {name}. {sms_note}", "success")
        else:
            flash(f"Appointment booked successfully for {name}. {sms_note}", "success")
        return redirect(url_for("appointments"))

    conn.close()
    return render_template("admit_patient.html", doctors=doctors, form=form, schedule_board=schedule_board, selected_date=today)


@app.route("/patients/<int:pid>")
@login_required
def patient_detail(pid):
    conn = get_db()
    patient = conn.execute(
        """
        SELECT p.*, u.full_name AS doctor_name, u.specialization AS doctor_specialization,
               r.full_name AS receptionist_name,
               pp.created_at AS profile_created_at, pp.updated_at AS profile_updated_at
        FROM patients p
        LEFT JOIN users u ON p.assigned_doctor=u.id
        LEFT JOIN users r ON p.admitted_by=r.id
        LEFT JOIN patient_profiles pp ON p.profile_id=pp.id
        WHERE p.id=?
        """,
        (pid,),
    ).fetchone()
    prescriptions = conn.execute(
        """
        SELECT pr.*, u.full_name AS doctor_name
        FROM prescriptions pr
        JOIN users u ON pr.doctor_id=u.id
        WHERE pr.patient_id=?
        ORDER BY pr.created_at DESC
        """,
        (pid,),
    ).fetchall()
    appointments = conn.execute(
        """
        SELECT a.*, u.full_name AS doctor_name, u.specialization AS doctor_specialization,
               u.availability_status, u.availability_note
        FROM appointments a
        JOIN users u ON a.doctor_id=u.id
        WHERE a.patient_id=?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """,
        (pid,),
    ).fetchall()
    visit_history = get_profile_visits(conn, patient["profile_id"]) if patient and patient["profile_id"] else []
    conn.close()
    if not patient:
        flash("Patient not found.", "danger")
        return redirect(url_for("patients"))
    return render_template(
        "patient_detail.html",
        patient=patient,
        prescriptions=prescriptions,
        appointments=appointments,
        visit_history=visit_history,
    )


@app.route("/patients/<int:pid>/update_status", methods=["POST"])
@login_required
def update_status(pid):
    new_status = request.form["status"]
    allowed = {"waiting", "in_consultation", "prescribed", "discharged"}
    if new_status not in allowed:
        flash("Invalid status.", "danger")
        return redirect(url_for("patient_detail", pid=pid))
    conn = get_db()
    conn.execute("UPDATE patients SET status=? WHERE id=?", (new_status, pid))
    if new_status == "in_consultation":
        conn.execute(
            """
            UPDATE appointments
            SET status='in_consultation'
            WHERE patient_id=? AND status IN ('scheduled', 'checked_in')
            """,
            (pid,),
        )
    elif new_status in ("prescribed", "discharged"):
        conn.execute(
            """
            UPDATE appointments
            SET status='completed'
            WHERE patient_id=? AND status IN ('scheduled', 'checked_in', 'in_consultation')
            """,
            (pid,),
        )
    conn.commit()
    conn.close()
    flash("Status updated.", "success")
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/doctor/queue")
@login_required
@role_required("doctor")
def doctor_queue():
    conn = get_db()
    doctor_id = session["user_id"]
    today = date.today().isoformat()
    doctor = conn.execute(
        """
        SELECT id, full_name, specialization, availability_status, availability_note, availability_updated_at
        FROM users
        WHERE id=?
        """,
        (doctor_id,),
    ).fetchone()
    patients = conn.execute(
        """
        SELECT p.*, a.id AS appointment_id, a.appointment_time, a.appointment_date, a.status AS appointment_status,
               a.payment_status, a.payment_mode, a.patient_alert_message
        FROM patients p
        LEFT JOIN appointments a
          ON p.id=a.patient_id
         AND a.doctor_id=?
         AND a.status IN ('scheduled', 'checked_in', 'in_consultation')
        WHERE p.assigned_doctor=?
          AND a.appointment_date=?
        ORDER BY a.appointment_time
        """,
        (doctor_id, doctor_id, today),
    ).fetchall()
    all_patients = conn.execute(
        """
        SELECT p.*, a.id AS appointment_id, a.appointment_time, a.appointment_date, a.status AS appointment_status
        FROM patients p
        LEFT JOIN appointments a ON p.id=a.patient_id AND a.doctor_id=?
        WHERE p.assigned_doctor=?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC, p.admitted_at DESC
        """,
        (doctor_id, doctor_id),
    ).fetchall()
    schedule_board = build_doctor_day_calendar(conn, doctor, today)
    conn.close()
    return render_template(
        "doctor_queue.html",
        doctor=doctor,
        patients=patients,
        all_patients=all_patients,
        today=today,
        schedule_board=schedule_board,
    )


@app.route("/doctor/availability", methods=["POST"])
@login_required
@role_required("doctor", "receptionist")
def update_doctor_availability():
    doctor_id = request.form.get("doctor_id") or session["user_id"]
    status = request.form.get("availability_status", "available")
    note = request.form.get("availability_note", "").strip()
    block_date = request.form.get("block_date", "").strip()
    block_start = request.form.get("block_start", "").strip()
    block_end = request.form.get("block_end", "").strip()

    if status not in ("available", "busy"):
        flash("Invalid doctor availability status.", "danger")
        return redirect(request.referrer or url_for("appointments"))

    if session["role"] == "doctor" and str(doctor_id) != str(session["user_id"]):
        flash("You can only update your own availability.", "danger")
        return redirect(url_for("doctor_queue"))

    conn = get_db()
    doctor = conn.execute("SELECT id, full_name FROM users WHERE id=? AND role='doctor'", (doctor_id,)).fetchone()
    if not doctor:
        conn.close()
        flash("Doctor not found.", "danger")
        return redirect(request.referrer or url_for("appointments"))

    conn.execute(
        """
        UPDATE users
        SET availability_status=?, availability_note=?, availability_updated_at=?
        WHERE id=?
        """,
        (status, note or None, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), doctor_id),
    )
    if status == "busy" and block_date and block_start and block_end and block_end > block_start:
        conn.execute(
            """
            INSERT INTO doctor_blocks (doctor_id, block_date, start_time, end_time, block_type, reason)
            VALUES (?, ?, ?, ?, 'busy', ?)
            """,
            (doctor_id, block_date, block_start, block_end, note or "Doctor unavailable"),
        )
        create_notification(
            conn,
            "receptionist",
            f"{doctor['full_name']} marked busy",
            f"{doctor['full_name']} is busy on {block_date} from {block_start} to {block_end}. {note or 'Reception should avoid assigning this time.'}",
        )
    elif status == "busy":
        create_notification(
            conn,
            "receptionist",
            f"{doctor['full_name']} marked busy",
            f"{doctor['full_name']} marked status as busy. {note or 'Check the doctor calendar before booking further appointments.'}",
        )
    conn.commit()
    conn.close()
    flash(f"{doctor['full_name']} marked as {status}.", "success")
    if session["role"] == "doctor":
        return redirect(url_for("doctor_queue"))
    return redirect(request.referrer or url_for("appointments"))


@app.route("/patients/<int:pid>/prescribe", methods=["GET", "POST"])
@login_required
@role_required("doctor")
def prescribe(pid):
    conn = get_db()
    patient = conn.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    if not patient:
        conn.close()
        flash("Patient not found.", "danger")
        return redirect(url_for("patients"))
    if request.method == "POST":
        diagnosis = request.form["diagnosis"].strip()
        medicines = request.form["medicines"].strip()
        instructions = request.form.get("instructions", "").strip()
        follow_up = request.form.get("follow_up_date", "") or None
        conn.execute(
            """
            INSERT INTO prescriptions (patient_id, doctor_id, diagnosis, medicines, instructions, follow_up_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pid, session["user_id"], diagnosis, medicines, instructions, follow_up),
        )
        conn.execute("UPDATE patients SET status='prescribed' WHERE id=?", (pid,))
        conn.execute(
            """
            UPDATE appointments
            SET status='completed'
            WHERE patient_id=? AND doctor_id=? AND status IN ('scheduled', 'checked_in', 'in_consultation')
            """,
            (pid, session["user_id"]),
        )
        conn.commit()
        conn.close()
        flash("Prescription saved successfully.", "success")
        return redirect(url_for("patient_detail", pid=pid))
    conn.close()
    template_cards = [{"name": key, "diagnosis": value["diagnosis"], "medicines": value["medicines"]} for key, value in PRESCRIPTION_TEMPLATES.items()]
    return render_template("prescribe.html", patient=patient, template_cards=template_cards)


@app.route("/calendar")
@login_required
def calendar_view():
    conn = get_db()
    selected_date = request.args.get("date") or date.today().isoformat()
    doctor_filter = request.args.get("doctor_id", "")
    all_doctors = get_doctors(conn)
    doctors = all_doctors
    if session["role"] == "doctor":
        doctors = [doctor for doctor in doctors if doctor["id"] == session["user_id"]]
    elif doctor_filter:
        doctors = [doctor for doctor in doctors if str(doctor["id"]) == doctor_filter]
    board = [build_doctor_day_calendar(conn, doctor, selected_date) for doctor in doctors]
    conn.close()
    return render_template("calendar.html", selected_date=selected_date, doctors=all_doctors if session["role"] == "receptionist" else doctors, board=board, doctor_filter=doctor_filter)


@app.route("/calendar/schedule", methods=["POST"])
@login_required
@role_required("doctor")
def update_schedule():
    conn = get_db()
    conn.execute(
        """
        UPDATE doctor_schedules
        SET work_start=?, work_end=?, lunch_start=?, lunch_end=?, slot_minutes=?
        WHERE doctor_id=?
        """,
        (
            request.form["work_start"],
            request.form["work_end"],
            request.form["lunch_start"],
            request.form["lunch_end"],
            int(request.form.get("slot_minutes") or DEFAULT_SLOT_MINUTES),
            session["user_id"],
        ),
    )
    conn.commit()
    conn.close()
    flash("Working hours and lunch schedule updated.", "success")
    return redirect(url_for("calendar_view", date=request.form.get("selected_date") or date.today().isoformat()))


@app.route("/calendar/block", methods=["POST"])
@login_required
@role_required("doctor", "receptionist")
def create_calendar_block():
    doctor_id = request.form["doctor_id"]
    if session["role"] == "doctor" and str(doctor_id) != str(session["user_id"]):
        flash("You can only manage your own time blocks.", "danger")
        return redirect(url_for("calendar_view"))
    if request.form["end_time"] <= request.form["start_time"]:
        flash("Block end time must be after the start time.", "danger")
        return redirect(url_for("calendar_view", date=request.form["block_date"]))
    conn = get_db()
    issue = validate_slot(conn, doctor_id, request.form["block_date"], request.form["start_time"])
    if issue and issue["kind"] in ("outside_hours", "lunch"):
        conn.close()
        flash(issue["message"], "danger")
        return redirect(url_for("calendar_view", date=request.form["block_date"]))
    conn.execute(
        """
        INSERT INTO doctor_blocks (doctor_id, block_date, start_time, end_time, block_type, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            doctor_id,
            request.form["block_date"],
            request.form["start_time"],
            request.form["end_time"],
            request.form.get("block_type") or "busy",
            request.form.get("reason", "").strip() or None,
        ),
    )
    doctor = conn.execute("SELECT full_name FROM users WHERE id=?", (doctor_id,)).fetchone()
    create_notification(
        conn,
        "receptionist",
        f"{doctor['full_name'] if doctor else 'Doctor'} block added",
        f"{doctor['full_name'] if doctor else 'Doctor'} blocked {request.form['block_date']} from {request.form['start_time']} to {request.form['end_time']}. {request.form.get('reason', '').strip() or 'No extra reason provided.'}",
    )
    conn.commit()
    conn.close()
    flash("Calendar block saved.", "success")
    return redirect(url_for("calendar_view", date=request.form["block_date"], doctor_id=doctor_id if session["role"] == "receptionist" else None))


@app.route("/notifications")
@login_required
@role_required("receptionist")
def notifications():
    conn = get_db()
    items = get_notifications(conn, "receptionist", limit=100, unread_only=False)
    conn.execute("UPDATE notifications SET is_read=1 WHERE target_role='receptionist' AND is_read=0")
    conn.commit()
    conn.close()
    return render_template("notifications.html", notifications=items)


@app.route("/appointments")
@login_required
def appointments():
    conn = get_db()
    today = date.today().isoformat()
    selected_date = request.args.get("date") or today
    doctors = get_doctors(conn)
    query = """
        SELECT a.*, p.name AS patient_name, p.phone AS patient_phone, p.visit_reason, p.required_specialization,
               u.full_name AS doctor_name, u.specialization AS doctor_specialization,
               u.availability_status, u.availability_note
        FROM appointments a
        JOIN patients p ON a.patient_id=p.id
        JOIN users u ON a.doctor_id=u.id
    """
    params = []
    if session["role"] == "doctor":
        query += " WHERE a.doctor_id=?"
        params.append(session["user_id"])
    query += " ORDER BY a.appointment_date DESC, a.appointment_time DESC"
    appts = conn.execute(query, params).fetchall()
    calendar_board = [
        build_doctor_day_calendar(conn, doctor, selected_date)
        for doctor in (doctors if session["role"] == "receptionist" else [doctor for doctor in doctors if doctor["id"] == session["user_id"]])
    ]
    conn.close()
    return render_template("appointments.html", appointments=appts, today=today, doctors=doctors, selected_date=selected_date, calendar_board=calendar_board)


@app.route("/appointments/<int:appointment_id>/payment", methods=["POST"])
@login_required
@role_required("receptionist")
def update_payment(appointment_id):
    payment_mode = request.form.get("payment_mode", "")
    transaction_id = request.form.get("transaction_id", "").strip()

    if payment_mode not in ("online", "cash"):
        flash("Select a valid payment mode.", "danger")
        return redirect(url_for("appointments"))
    if payment_mode == "online" and not transaction_id:
        flash("Transaction ID is required for online payments.", "danger")
        return redirect(url_for("appointments"))

    conn = get_db()
    conn.execute(
        """
        UPDATE appointments
        SET payment_status='paid', payment_mode=?, transaction_id=?
        WHERE id=?
        """,
        (payment_mode, transaction_id or None, appointment_id),
    )
    conn.commit()
    conn.close()
    flash("Payment details updated.", "success")
    return redirect(url_for("appointments"))


@app.route("/appointments/<int:appointment_id>/reschedule", methods=["POST"])
@login_required
@role_required("receptionist", "doctor")
def reschedule_appointment(appointment_id):
    new_date = request.form.get("new_date", "")
    new_time = request.form.get("new_time", "")
    reason = request.form.get("reason", "").strip()

    if not new_date or not new_time:
        flash("Please choose the new appointment date and time.", "danger")
        return redirect(url_for("appointments"))

    conn = get_db()
    appt = conn.execute(
        """
        SELECT a.*, p.name AS patient_name, p.phone AS patient_phone, u.full_name AS doctor_name
        FROM appointments a
        JOIN patients p ON a.patient_id=p.id
        JOIN users u ON a.doctor_id=u.id
        WHERE a.id=?
        """,
        (appointment_id,),
    ).fetchone()
    if not appt:
        conn.close()
        flash("Appointment not found.", "danger")
        return redirect(url_for("appointments"))

    if session["role"] == "doctor" and appt["doctor_id"] != session["user_id"]:
        conn.close()
        flash("You can only reschedule your own appointments.", "danger")
        return redirect(url_for("appointments"))

    slot_issue = validate_slot(conn, appt["doctor_id"], new_date, new_time, ignore_appointment_id=appointment_id)
    if slot_issue:
        conn.close()
        flash(slot_issue["message"], "danger")
        return redirect(url_for("appointments"))

    alert_message = format_alert_message(
        appt["patient_name"],
        appt["doctor_name"],
        new_date,
        new_time,
        reason,
    )
    conn.execute(
        """
        UPDATE appointments
        SET appointment_date=?, appointment_time=?, status='scheduled',
            reschedule_reason=?, cancellation_deadline=?, patient_alert_message=?, patient_alert_sent_at=?
        WHERE id=?
        """,
        (
            new_date,
            new_time,
            reason or None,
            build_cancellation_deadline(new_date, new_time),
            alert_message,
            None,
            appointment_id,
        ),
    )
    conn.execute("UPDATE patients SET status='waiting' WHERE id=?", (appt["patient_id"],))
    sent, sms_note = send_sms_notification(appt["patient_phone"], alert_message)
    if not sent:
        conn.execute(
            """
            UPDATE appointments
            SET patient_alert_message=?, patient_alert_sent_at=NULL
            WHERE id=?
            """,
            (f"{alert_message} [{sms_note}]", appointment_id),
        )
    else:
        conn.execute(
            """
            UPDATE appointments
            SET patient_alert_sent_at=?
            WHERE id=?
            """,
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), appointment_id),
        )
    conn.commit()
    conn.close()
    flash(f"Appointment rescheduled. {sms_note}", "success")
    return redirect(url_for("appointments"))


@app.route("/appointments/<int:appointment_id>/cancel", methods=["POST"])
@login_required
@role_required("receptionist")
def cancel_appointment(appointment_id):
    reason = request.form.get("reason", "").strip()
    conn = get_db()
    appt = conn.execute(
        """
        SELECT a.*, p.name AS patient_name
        FROM appointments a
        JOIN patients p ON a.patient_id=p.id
        WHERE a.id=?
        """,
        (appointment_id,),
    ).fetchone()
    if not appt:
        conn.close()
        flash("Appointment not found.", "danger")
        return redirect(url_for("appointments"))

    appointment_dt = appointment_datetime(appt["appointment_date"], appt["appointment_time"])
    refund_status = "eligible" if datetime.now() <= appointment_dt - timedelta(hours=CANCELLATION_WINDOW_HOURS) else "not_eligible"

    conn.execute(
        """
        UPDATE appointments
        SET status='cancelled', cancelled_at=?, cancelled_by=?, refund_status=?, reschedule_reason=?
        WHERE id=?
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            session["user_id"],
            refund_status,
            reason or None,
            appointment_id,
        ),
    )
    conn.execute("UPDATE patients SET status='discharged' WHERE id=?", (appt["patient_id"],))
    doctor = conn.execute("SELECT full_name FROM users WHERE id=?", (appt["doctor_id"],)).fetchone()
    cancel_message = build_cancellation_message(
        appt["patient_name"] if "patient_name" in appt.keys() else "Patient",
        doctor["full_name"] if doctor else "doctor",
        appt["appointment_date"],
        appt["appointment_time"],
        refund_status,
    )
    sent, sms_note = send_sms_notification(appt["notification_phone"], cancel_message)
    conn.execute(
        """
        UPDATE appointments
        SET patient_alert_message=?, patient_alert_sent_at=?
        WHERE id=?
        """,
        (
            cancel_message if sent else f"{cancel_message} [{sms_note}]",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S") if sent else None,
            appointment_id,
        ),
    )
    conn.commit()
    conn.close()
    flash(
        f"Appointment cancelled. Refund status: {'available' if refund_status == 'eligible' else 'not available'}. {sms_note}",
        "success",
    )
    return redirect(url_for("appointments"))


@app.route("/prescriptions/<int:prescription_id>/pdf")
@login_required
def prescription_pdf(prescription_id):
    conn = get_db()
    presc = conn.execute(
        """
        SELECT pr.*, p.name AS patient_name, p.age, p.gender, p.blood_group, p.phone,
               u.full_name AS doctor_name
        FROM prescriptions pr
        JOIN patients p ON pr.patient_id=p.id
        JOIN users u ON pr.doctor_id=u.id
        WHERE pr.id=?
        """,
        (prescription_id,),
    ).fetchone()
    conn.close()
    if not presc:
        flash("Prescription not found.", "danger")
        return redirect(url_for("patients"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    brand = colors.HexColor("#1a56db")
    dark = colors.HexColor("#1e2a3a")
    muted = colors.HexColor("#6b7280")

    title_style = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=22, textColor=brand, alignment=TA_CENTER)
    sub_style = ParagraphStyle("sub", fontName="Helvetica", fontSize=10, textColor=muted, alignment=TA_CENTER)
    h2_style = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, textColor=dark, spaceBefore=12)
    body_style = ParagraphStyle("body", fontName="Helvetica", fontSize=11, textColor=dark, leading=16)
    rx_style = ParagraphStyle("rx", fontName="Helvetica", fontSize=11, textColor=dark, leading=18, leftIndent=20)

    story = []
    story.append(Paragraph("MediCare Hospital", title_style))
    story.append(Paragraph("Quality Healthcare for Everyone", sub_style))
    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=2, color=brand))
    story.append(Spacer(1, 0.3 * cm))
    story.append(
        Paragraph(
            "PRESCRIPTION",
            ParagraphStyle("rx_title", fontName="Helvetica-Bold", fontSize=14, textColor=brand, alignment=TA_CENTER),
        )
    )
    story.append(Spacer(1, 0.5 * cm))

    issued = datetime.strptime(presc["created_at"], "%Y-%m-%d %H:%M:%S").strftime("%d %b %Y, %I:%M %p")
    info_data = [
        ["Patient Name", presc["patient_name"], "Doctor", presc["doctor_name"]],
        ["Age / Gender", f"{presc['age']} yrs / {presc['gender']}", "Date", issued],
        ["Blood Group", presc["blood_group"] or "-", "Phone", presc["phone"] or "-"],
    ]
    info_table = Table(info_data, colWidths=[3.5 * cm, 7 * cm, 3 * cm, 4.5 * cm])
    info_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (0, -1), muted),
                ("TEXTCOLOR", (2, 0), (2, -1), muted),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f0f7ff"), colors.white]),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bfdbfe")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dbeafe")),
            ]
        )
    )
    story.append(info_table)
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#bfdbfe")))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("Diagnosis", h2_style))
    story.append(Paragraph(presc["diagnosis"], body_style))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Medicines Prescribed", h2_style))
    for line in presc["medicines"].split("\n"):
        if line.strip():
            story.append(Paragraph(f"- {line.strip()}", rx_style))
    story.append(Spacer(1, 0.4 * cm))

    if presc["instructions"]:
        story.append(Paragraph("Instructions", h2_style))
        story.append(Paragraph(presc["instructions"], body_style))
        story.append(Spacer(1, 0.4 * cm))

    if presc["follow_up_date"]:
        follow_up = datetime.strptime(presc["follow_up_date"], "%Y-%m-%d").strftime("%d %b %Y")
        story.append(Paragraph(f"Follow-up Date: <b>{follow_up}</b>", body_style))
        story.append(Spacer(1, 0.4 * cm))

    story.append(Spacer(1, 1 * cm))
    story.append(HRFlowable(width="40%", thickness=0.5, color=dark, hAlign="RIGHT"))
    story.append(Paragraph(presc["doctor_name"], ParagraphStyle("sign", fontName="Helvetica-Bold", fontSize=11, textColor=dark, alignment=TA_RIGHT)))
    story.append(Paragraph("Authorized Signature", ParagraphStyle("sign2", fontName="Helvetica", fontSize=9, textColor=muted, alignment=TA_RIGHT)))

    doc.build(story)
    buf.seek(0)
    response = make_response(buf.read())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"inline; filename=prescription_{prescription_id}.pdf"
    return response


init_db()


if __name__ == "__main__":
    app.run(debug=True)
