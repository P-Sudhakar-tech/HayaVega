"""Security-focused local authentication primitives for Mangoo."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import pbkdf2_hmac, scrypt, sha256
from hmac import compare_digest, new
from pathlib import Path
import base64
import os
import re
import secrets
import sqlite3
import struct
import time

from .db import connect, user_id_definition
from .audit import record_audit_event


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
DEFAULT_SESSION_SECONDS = 8 * 60 * 60
PASSWORD_RESET_TOKEN_SECONDS = 60 * 60
EMAIL_VERIFICATION_TOKEN_SECONDS = 24 * 60 * 60
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PASSWORD_RESET_PURPOSE = "password_reset"
_EMAIL_VERIFICATION_PURPOSE = "email_verification"


class UserAlreadyExistsError(ValueError):
    """Raised when a username or email already belongs to a user."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    email: str
    is_superuser: bool
    is_active: bool
    two_factor_enabled: bool = False
    email_verified: bool = False


@dataclass(frozen=True)
class SessionSummary:
    """Non-sensitive metadata about one active session; never includes the token."""
    id: int
    created_at: str
    expires_at: int


def _connect(database_path: str | Path | dict):
    return connect(database_path)


def initialize_auth_db(database_path: str | Path | dict) -> None:
    """Create or upgrade the authentication schema without external services."""
    connection = _connect(database_path)
    try:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS mangoo_users (
                id {user_id_definition(connection.engine)},
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_superuser INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until INTEGER,
                totp_secret TEXT,
                totp_enabled INTEGER NOT NULL DEFAULT 0,
                email_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        if connection.engine == "sqlite3":
            existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(mangoo_users)")}
            additions = {"failed_attempts": "INTEGER NOT NULL DEFAULT 0", "locked_until": "INTEGER", "totp_secret": "TEXT", "totp_enabled": "INTEGER NOT NULL DEFAULT 0", "email_verified": "INTEGER NOT NULL DEFAULT 0"}
            for name, definition in additions.items():
                if name not in existing_columns:
                    connection.execute(f"ALTER TABLE mangoo_users ADD COLUMN {name} {definition}")
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS mangoo_sessions (
                id {user_id_definition(connection.engine)},
                user_id INTEGER NOT NULL REFERENCES mangoo_users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS mangoo_auth_tokens (
                id {user_id_definition(connection.engine)},
                user_id INTEGER NOT NULL REFERENCES mangoo_users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                consumed_at INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    requirements = (any(char.islower() for char in password), any(char.isupper() for char in password), any(char.isdigit() for char in password), any(not char.isalnum() for char in password))
    if not all(requirements):
        raise ValueError("Password must contain lowercase, uppercase, number, and symbol characters.")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = os.urandom(16)
    derived_key = scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode("ascii"), base64.b64encode(derived_key).decode("ascii")
    )


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        parts = encoded_password.split("$")
        if parts[0] == "scrypt" and len(parts) == 6:
            _, n, r, p, encoded_salt, encoded_key = parts
            derived_key = scrypt(password.encode("utf-8"), salt=base64.b64decode(encoded_salt), n=int(n), r=int(r), p=int(p), dklen=32)
            return compare_digest(base64.b64encode(derived_key).decode("ascii"), encoded_key)
        # Retain verification for legacy Mangoo PBKDF2 records, then upgrade on next login.
        if parts[0] == "pbkdf2_sha256" and len(parts) == 4:
            _, iterations, encoded_salt, encoded_key = parts
            derived_key = pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(encoded_salt), int(iterations))
            return compare_digest(base64.b64encode(derived_key).decode("ascii"), encoded_key)
    except (ValueError, TypeError):
        pass
    return False


def _user_from_row(row: sqlite3.Row) -> User:
    return User(row["id"], row["username"], row["email"], bool(row["is_superuser"]), bool(row["is_active"]), bool(row["totp_enabled"]), bool(row["email_verified"]))


def get_user_by_username(database_path: str | Path | dict, username: str) -> User | None:
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        row = connection.execute("SELECT * FROM mangoo_users WHERE username = ?", (username,)).fetchone()
        return _user_from_row(row) if row is not None else None
    finally:
        connection.close()


def create_user(database_path: str | Path | dict, username: str, email: str, password: str, *, is_superuser: bool = False) -> User:
    username, email = username.strip(), email.strip().lower()
    if not username or len(username) > 150:
        raise ValueError("Username is required and must be 150 characters or fewer.")
    if not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError("A valid email address is required.")
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        insert = "INSERT INTO mangoo_users (username, email, password_hash, is_superuser) VALUES (?, ?, ?, ?)"
        cursor = connection.execute(
            insert + (" RETURNING id" if connection.engine == "postgresql" else ""),
            (username, email, hash_password(password), int(is_superuser)),
        )
        connection.commit()
        identifier = cursor.fetchone()["id"] if connection.engine == "postgresql" else cursor.lastrowid
        user = User(identifier, username, email, is_superuser, True)
        record_audit_event(database_path, "auth.user_created", actor_id=user.id, metadata={"is_superuser": is_superuser})
        return user
    except sqlite3.IntegrityError as error:
        raise UserAlreadyExistsError("A user with that username or email already exists.") from error
    finally:
        connection.close()


def create_superuser(database_path: str | Path | dict, username: str, email: str, password: str) -> User:
    return create_user(database_path, username, email, password, is_superuser=True)


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_code(secret: str, timestamp: int) -> str:
    normalized = secret.upper() + "=" * (-len(secret) % 8)
    counter = timestamp // 30
    digest = new(base64.b32decode(normalized), struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret: str, code: str, timestamp: int | None = None) -> bool:
    now = int(time.time()) if timestamp is None else timestamp
    return any(compare_digest(_totp_code(secret, now + step * 30), str(code)) for step in (-1, 0, 1))


def enable_totp(database_path: str | Path | dict, username: str) -> str:
    initialize_auth_db(database_path)
    secret = generate_totp_secret()
    connection = _connect(database_path)
    try:
        cursor = connection.execute("UPDATE mangoo_users SET totp_secret = ?, totp_enabled = 1 WHERE username = ?", (secret, username))
        connection.commit()
        if cursor.rowcount != 1:
            raise ValueError("User was not found.")
        return secret
    finally:
        connection.close()


def _record_failure(connection, row, now: int) -> None:
    attempts = row["failed_attempts"] + 1
    locked_until = now + LOCKOUT_SECONDS if attempts >= MAX_FAILED_ATTEMPTS else None
    connection.execute("UPDATE mangoo_users SET failed_attempts = ?, locked_until = ? WHERE id = ?", (attempts, locked_until, row["id"]))
    connection.commit()


def authenticate(database_path: str | Path | dict, username: str, password: str, totp_code: str | None = None) -> User | None:
    initialize_auth_db(database_path)
    now = int(time.time())
    connection = _connect(database_path)
    try:
        row = connection.execute("SELECT * FROM mangoo_users WHERE username = ?", (username,)).fetchone()
        if row is None or not row["is_active"]:
            record_audit_event(database_path, "auth.login_failed", metadata={"reason": "invalid_credentials"})
            return None
        if row["locked_until"] and row["locked_until"] > now:
            record_audit_event(database_path, "auth.login_failed", actor_id=row["id"], metadata={"reason": "locked"})
            return None
        password_valid = verify_password(password, row["password_hash"])
        totp_valid = not row["totp_enabled"] or (totp_code is not None and verify_totp(row["totp_secret"], totp_code, now))
        if not password_valid or not totp_valid:
            _record_failure(connection, row, now)
            record_audit_event(database_path, "auth.login_failed", actor_id=row["id"], metadata={"reason": "invalid_credentials"})
            return None
        connection.execute("UPDATE mangoo_users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (row["id"],))
        if row["password_hash"].startswith("pbkdf2_sha256$"):
            connection.execute("UPDATE mangoo_users SET password_hash = ? WHERE id = ?", (hash_password(password), row["id"]))
        connection.commit()
        user = _user_from_row(row)
        record_audit_event(database_path, "auth.login_succeeded", actor_id=user.id)
        return user
    finally:
        connection.close()


def create_session(database_path: str | Path | dict, user: User, duration_seconds: int = DEFAULT_SESSION_SECONDS) -> str:
    if not user.is_active:
        raise ValueError("Cannot create a session for an inactive user.")
    initialize_auth_db(database_path)
    token = secrets.token_urlsafe(48)
    connection = _connect(database_path)
    try:
        connection.execute("INSERT INTO mangoo_sessions (user_id, token_hash, expires_at) VALUES (?, ?, ?)", (user.id, sha256(token.encode()).hexdigest(), int(time.time()) + duration_seconds))
        connection.commit()
        record_audit_event(database_path, "auth.session_created", actor_id=user.id)
        return token
    finally:
        connection.close()


def authenticate_session(database_path: str | Path | dict, token: str) -> User | None:
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        now = int(time.time())
        connection.execute("DELETE FROM mangoo_sessions WHERE expires_at <= ?", (now,))
        row = connection.execute(
            "SELECT u.* FROM mangoo_sessions s JOIN mangoo_users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
            (sha256(token.encode()).hexdigest(), now),
        ).fetchone()
        connection.commit()
        return _user_from_row(row) if row is not None and row["is_active"] else None
    finally:
        connection.close()


def revoke_session(database_path: str | Path | dict, token: str) -> None:
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        row = connection.execute("SELECT user_id FROM mangoo_sessions WHERE token_hash = ?", (sha256(token.encode()).hexdigest(),)).fetchone()
        connection.execute("DELETE FROM mangoo_sessions WHERE token_hash = ?", (sha256(token.encode()).hexdigest(),))
        connection.commit()
        if row is not None:
            record_audit_event(database_path, "auth.session_revoked", actor_id=row["user_id"])
    finally:
        connection.close()


def list_sessions(database_path: str | Path | dict, user: User) -> list[SessionSummary]:
    """Return non-sensitive metadata for a user's active sessions; never the token itself."""
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        now = int(time.time())
        connection.execute("DELETE FROM mangoo_sessions WHERE expires_at <= ?", (now,))
        connection.commit()
        rows = connection.execute(
            "SELECT id, expires_at, created_at FROM mangoo_sessions WHERE user_id = ? ORDER BY created_at DESC", (user.id,)
        ).fetchall()
        return [SessionSummary(row["id"], row["created_at"], row["expires_at"]) for row in rows]
    finally:
        connection.close()


def revoke_all_sessions(database_path: str | Path | dict, user: User, *, except_token: str | None = None) -> int:
    """Revoke every session for a user, optionally keeping one (the caller's current token)."""
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        if except_token:
            cursor = connection.execute(
                "DELETE FROM mangoo_sessions WHERE user_id = ? AND token_hash != ?",
                (user.id, sha256(except_token.encode()).hexdigest()),
            )
        else:
            cursor = connection.execute("DELETE FROM mangoo_sessions WHERE user_id = ?", (user.id,))
        connection.commit()
        revoked = cursor.rowcount
        if revoked:
            record_audit_event(database_path, "auth.sessions_revoked_all", actor_id=user.id, metadata={"count": revoked})
        return revoked
    finally:
        connection.close()


def rotate_session(database_path: str | Path | dict, token: str, duration_seconds: int = DEFAULT_SESSION_SECONDS) -> str | None:
    """Issue a fresh token for an existing session and invalidate the old one; None if the token is invalid."""
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        now = int(time.time())
        old_hash = sha256(token.encode()).hexdigest()
        row = connection.execute("SELECT user_id FROM mangoo_sessions WHERE token_hash = ? AND expires_at > ?", (old_hash, now)).fetchone()
        if row is None:
            return None
        new_token = secrets.token_urlsafe(48)
        connection.execute(
            "UPDATE mangoo_sessions SET token_hash = ?, expires_at = ? WHERE token_hash = ?",
            (sha256(new_token.encode()).hexdigest(), now + duration_seconds, old_hash),
        )
        connection.commit()
        record_audit_event(database_path, "auth.session_rotated", actor_id=row["user_id"])
        return new_token
    finally:
        connection.close()


def _issue_auth_token(database_path: str | Path | dict, user: User, purpose: str, duration_seconds: int) -> str:
    initialize_auth_db(database_path)
    token = secrets.token_urlsafe(32)
    connection = _connect(database_path)
    try:
        connection.execute(
            "INSERT INTO mangoo_auth_tokens (user_id, purpose, token_hash, expires_at) VALUES (?, ?, ?, ?)",
            (user.id, purpose, sha256(token.encode()).hexdigest(), int(time.time()) + duration_seconds),
        )
        connection.commit()
        return token
    finally:
        connection.close()


def _consume_auth_token(database_path: str | Path | dict, token: str, purpose: str):
    """Validate and single-use-consume a token; return the owning user's row, or None."""
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        now = int(time.time())
        row = connection.execute(
            "SELECT t.id AS token_id, u.* FROM mangoo_auth_tokens t JOIN mangoo_users u ON u.id = t.user_id "
            "WHERE t.token_hash = ? AND t.purpose = ? AND t.consumed_at IS NULL AND t.expires_at > ?",
            (sha256(token.encode()).hexdigest(), purpose, now),
        ).fetchone()
        if row is None:
            return None
        connection.execute("UPDATE mangoo_auth_tokens SET consumed_at = ? WHERE id = ?", (now, row["token_id"]))
        connection.commit()
        return row
    finally:
        connection.close()


def create_password_reset_token(database_path: str | Path | dict, user: User) -> str:
    token = _issue_auth_token(database_path, user, _PASSWORD_RESET_PURPOSE, PASSWORD_RESET_TOKEN_SECONDS)
    record_audit_event(database_path, "auth.password_reset_requested", actor_id=user.id)
    return token


def reset_password(database_path: str | Path | dict, token: str, new_password: str) -> User | None:
    """Consume a password-reset token, set the new password, and revoke every existing session."""
    row = _consume_auth_token(database_path, token, _PASSWORD_RESET_PURPOSE)
    if row is None:
        return None
    connection = _connect(database_path)
    try:
        connection.execute(
            "UPDATE mangoo_users SET password_hash = ?, failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (hash_password(new_password), row["id"]),
        )
        connection.commit()
    finally:
        connection.close()
    user = _user_from_row(row)
    revoke_all_sessions(database_path, user)
    record_audit_event(database_path, "auth.password_reset_completed", actor_id=user.id)
    return user


def create_email_verification_token(database_path: str | Path | dict, user: User) -> str:
    token = _issue_auth_token(database_path, user, _EMAIL_VERIFICATION_PURPOSE, EMAIL_VERIFICATION_TOKEN_SECONDS)
    record_audit_event(database_path, "auth.email_verification_requested", actor_id=user.id)
    return token


def verify_email(database_path: str | Path | dict, token: str) -> User | None:
    """Consume an email-verification token and mark the owning account verified."""
    row = _consume_auth_token(database_path, token, _EMAIL_VERIFICATION_PURPOSE)
    if row is None:
        return None
    connection = _connect(database_path)
    try:
        connection.execute("UPDATE mangoo_users SET email_verified = 1 WHERE id = ?", (row["id"],))
        connection.commit()
    finally:
        connection.close()
    record_audit_event(database_path, "auth.email_verified", actor_id=row["id"])
    return User(row["id"], row["username"], row["email"], bool(row["is_superuser"]), bool(row["is_active"]), bool(row["totp_enabled"]), True)
