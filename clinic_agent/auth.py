"""Account and session persistence for the patient and doctor portals."""
from __future__ import annotations

import hashlib
import secrets
import smtplib
import time
import uuid
from email.message import EmailMessage

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email

from .clinic_db import connect
from .doctor_schedule import DEPARTMENTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('patient', 'doctor')),
    department TEXT,
    password_hash TEXT,
    patient_id TEXT UNIQUE REFERENCES patients(patient_id),
    doctor_id TEXT UNIQUE REFERENCES doctors(doctor_id)
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    digest TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    expires INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_token_account ON auth_tokens(account_id, purpose);
CREATE TABLE IF NOT EXISTS auth_limits (
    scope TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL,
    expires INTEGER NOT NULL
);
"""
PASSWORDS = PasswordHasher()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))
SESSION_LIFETIME = 12 * 60 * 60


class AuthError(ValueError):
    pass


def normalize_email(value):
    try:
        return validate_email(value.strip(), check_deliverability=False, test_environment=True).normalized.casefold()
    except (AttributeError, EmailNotValidError) as exc:
        raise AuthError("Enter a valid email address.") from exc


def clean_profile(name, role, department=None):
    name = " ".join(str(name or "").split())
    if not 2 <= len(name) <= 100:
        raise AuthError("Enter a name between 2 and 100 characters.")
    if role not in ("patient", "doctor"):
        raise AuthError("Choose a valid account type.")
    if role == "doctor" and department not in DEPARTMENTS:
        raise AuthError("Choose a clinic department.")
    return name


def password_hash(password):
    if not 12 <= len(password or "") <= 256:
        raise AuthError("Use a password between 12 and 256 characters.")
    return PASSWORDS.hash(password)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Accounts:
    def __init__(self, db_path):
        self.db_path = db_path

    def prepare(self):
        with connect(self.db_path) as conn:
            conn.executescript(SCHEMA)

    @staticmethod
    def _provision(conn, account):
        profile_id = f"U-{uuid.uuid4().hex[:16]}"
        if account["role"] == "doctor":
            conn.execute(
                "INSERT INTO doctors VALUES (?, ?, ?, ?, 0, ?)",
                (profile_id, account["name"], account["department"], "", int(account["department"] == "Cardiology")),
            )
            conn.execute("UPDATE accounts SET doctor_id = ? WHERE id = ?", (profile_id, account["id"]))
        else:
            conn.execute("INSERT INTO patients VALUES (?, ?, 1)", (profile_id, account["name"]))
            conn.execute("UPDATE accounts SET patient_id = ? WHERE id = ?", (profile_id, account["id"]))

    @staticmethod
    def _token(conn, purpose, account_id, seconds):
        value = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM auth_tokens WHERE expires <= ?", (int(time.time()),))
        # Keep the payload column for databases created by older versions.
        conn.execute(
            "INSERT INTO auth_tokens (digest, purpose, account_id, expires, data) VALUES (?, ?, ?, ?, '{}')",
            (digest(value), purpose, account_id, int(time.time()) + seconds),
        )
        return value

    def create_session(self, account_id, previous_token=None):
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if previous_token:
                conn.execute("DELETE FROM auth_tokens WHERE digest = ? AND purpose = 'session'",
                             (digest(previous_token),))
            return self._token(conn, "session", account_id, SESSION_LIFETIME)

    def revoke_session(self, token):
        if token:
            with connect(self.db_path) as conn:
                conn.execute("DELETE FROM auth_tokens WHERE digest = ? AND purpose = 'session'", (digest(token),))

    def register(self, name, email, password, role, department=None):
        name = clean_profile(name, role, department)
        email = normalize_email(email)
        hashed = password_hash(password)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone():
                raise AuthError("An account with this email already exists. Sign in or reset your password.")
            account_id = conn.execute(
                "INSERT INTO accounts(email, name, role, department, password_hash) VALUES (?, ?, ?, ?, ?)",
                (email, name, role, department, hashed),
            ).lastrowid
            account = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            self._provision(conn, account)
            return dict(conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())

    def request_reset(self, email):
        email = normalize_email(email)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute(
                "SELECT id FROM accounts WHERE email = ? AND (patient_id IS NOT NULL OR doctor_id IS NOT NULL)",
                (email,),
            ).fetchone()
            if not user:
                return email, None
            conn.execute("DELETE FROM auth_tokens WHERE account_id = ? AND purpose = 'reset'", (user["id"],))
            return email, self._token(conn, "reset", user["id"], 1800)

    def reset(self, token, password):
        hashed = password_hash(password)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            entry = conn.execute(
                "SELECT account_id FROM auth_tokens WHERE digest = ? AND purpose = 'reset' AND expires > ?",
                (digest(token or ""), int(time.time())),
            ).fetchone()
            if not entry:
                return False
            conn.execute("UPDATE accounts SET password_hash = ? WHERE id = ?", (hashed, entry["account_id"]))
            conn.execute("DELETE FROM auth_tokens WHERE account_id = ? AND purpose IN ('session', 'reset')",
                         (entry["account_id"],))
            return True

    def check_password(self, email, password):
        try:
            email = normalize_email(email)
        except AuthError:
            email = ""
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        try:
            hashed = (row["password_hash"] if row else None) or DUMMY_HASH
            matched = len(password) <= 256 and PASSWORDS.verify(hashed, password)
        except (InvalidHashError, VerificationError):
            matched = False
        if not row or not row["password_hash"] or not matched:
            raise AuthError("Check your email and password and try again.")
        if row["patient_id"] or row["doctor_id"]:
            return dict(row)

        # Accounts awaiting activation in older versions can finish setup with their password.
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (row["id"],)).fetchone()
            if not (row["patient_id"] or row["doctor_id"]):
                self._provision(conn, row)
                row = conn.execute("SELECT * FROM accounts WHERE id = ?", (row["id"],)).fetchone()
            return dict(row)

    def user_for_session(self, token):
        if not token:
            return None
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT a.* FROM accounts a JOIN auth_tokens t ON a.id = t.account_id "
                "WHERE t.digest = ? AND t.purpose = 'session' AND t.expires > ? "
                "AND a.password_hash IS NOT NULL AND (a.patient_id IS NOT NULL OR a.doctor_id IS NOT NULL)",
                (digest(token), int(time.time())),
            ).fetchone()
        return dict(row) if row else None

    def rate_limit(self, scope, limit=10, window=600):
        now = int(time.time())
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM auth_limits WHERE expires <= ?", (now,))
            conn.execute(
                "INSERT INTO auth_limits VALUES (?, 1, ?) "
                "ON CONFLICT(scope) DO UPDATE SET attempts = attempts + 1", (scope, now + window),
            )
            return conn.execute("SELECT attempts FROM auth_limits WHERE scope = ?", (scope,)).fetchone()[0] <= limit

    def attach_fixture(self, role, profile_id, email, password):
        if role not in ("doctor", "patient"):
            raise AuthError("Choose a patient or doctor profile.")
        table, field = ("doctors", "doctor_id") if role == "doctor" else ("patients", "patient_id")
        email, hashed = normalize_email(email), password_hash(password)
        with connect(self.db_path) as conn:
            row = conn.execute(f"SELECT name FROM {table} WHERE {field} = ?", (profile_id,)).fetchone()
            if not row:
                raise AuthError("That clinic profile does not exist.")
            conn.execute(
                f"INSERT INTO accounts(email, name, role, password_hash, {field}) VALUES (?, ?, ?, ?, ?)",
                (email, row["name"], role, hashed, profile_id),
            )


def send_reset_email(settings, email, token, demo_mail=False):
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = email
    message["Subject"] = "Reset your CareBridge password"
    message.set_content(
        f"Open the following link to continue:\n\n{settings.site_origin}/reset-password?token={token}\n",
        cte="7bit",
    )
    if demo_mail and not settings.smtp_host:
        outbox = settings.clinic_db.parent / "mailbox"
        outbox.mkdir(parents=True, exist_ok=True)
        path = outbox / f"{time.time_ns()}-reset.eml"
        path.write_bytes(message.as_bytes())
        return
    if not settings.smtp_host:
        raise AuthError("Account email is unavailable. Check the clinic's SMTP settings.")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=8) as client:
            if settings.smtp_starttls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise AuthError("Account email could not be sent. Try again shortly.") from exc
