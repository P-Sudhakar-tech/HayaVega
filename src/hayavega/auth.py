"""Security-focused local authentication primitives for Hayavega."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sqlite3
import struct
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import pbkdf2_hmac, scrypt, sha256
from hmac import compare_digest, new
from pathlib import Path

from . import crypto
from .audit import record_audit_event
from .db import connect, normalize_config, user_id_definition

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
class Group:
    """A named set of permission codenames (e.g. ``"blog.add_post"``) --
    a lightweight analog of Django's ``Group``/``Permission`` models, as
    a JSON string list rather than a separate relational
    permission/content-type schema (``contenttypes`` is out of scope)."""

    id: int
    name: str
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class SessionSummary:
    """Non-sensitive metadata about one active session; never includes the token."""

    id: int
    created_at: str
    expires_at: int
    ip_address: str | None = None
    user_agent: str | None = None


def _connect(database_path: str | Path | dict):
    return connect(database_path)


_INITIALIZED_AUTH_DBS: set[tuple] = set()


def _auth_db_key(database_path: str | Path | dict) -> tuple:
    config = normalize_config(database_path)
    return tuple(sorted((key, str(value)) for key, value in config.items()))


_SESSION_SWEEP_INTERVAL_SECONDS = 60
_LAST_SESSION_SWEEP: dict[tuple, float] = {}


def _sweep_expired_sessions_if_due(connection, database_path: str | Path | dict, now: int) -> None:
    """Delete expired `hayavega_sessions` rows, but at most once per
    `_SESSION_SWEEP_INTERVAL_SECONDS` per (process, database) rather than
    on every call -- `authenticate_session`/`list_sessions` previously ran
    this unconditionally, making every authenticated request pay for a
    write+commit on what is otherwise a read. Safe to skip most of the
    time: the SELECT callers actually use always filters on
    `expires_at > ?` itself, so a stale-but-not-yet-swept row can never be
    treated as a valid session -- this is pure housekeeping, not a
    correctness dependency. Unlocked, same as `_INITIALIZED_AUTH_DBS`: a
    race just means an occasional redundant sweep, never a missed one.
    """
    key = _auth_db_key(database_path)
    last_swept = _LAST_SESSION_SWEEP.get(key, 0.0)
    if now - last_swept < _SESSION_SWEEP_INTERVAL_SECONDS:
        return
    _LAST_SESSION_SWEEP[key] = now
    connection.execute("DELETE FROM hayavega_sessions WHERE expires_at <= ?", (now,))


def initialize_auth_db(database_path: str | Path | dict) -> None:
    """Create or upgrade the authentication schema without external services.

    Called from every auth operation in this module (login, session checks,
    user/group creation, ...), so the "already initialized" case -- true for
    nearly every call after the first -- must not pay for a connection and a
    full CREATE TABLE/PRAGMA/ALTER TABLE round trip every time. Cached per
    (normalized database config) for the life of the process instead, the
    same pattern `models.Collection.prepare()`'s `_PREPARED_TABLES` cache
    already uses: a schema doesn't change without a migration, a separate,
    deliberate operation this cache has no effect on. The cache is only
    populated after the DDL actually succeeds (added below, outside the
    try/finally), so a failed attempt is never mistaken for a completed one
    and the next call retries it.
    """
    key = _auth_db_key(database_path)
    if key in _INITIALIZED_AUTH_DBS:
        return
    connection = _connect(database_path)
    try:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS hayavega_users (
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
            existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(hayavega_users)")}
            additions = {"failed_attempts": "INTEGER NOT NULL DEFAULT 0", "locked_until": "INTEGER", "totp_secret": "TEXT", "totp_enabled": "INTEGER NOT NULL DEFAULT 0", "email_verified": "INTEGER NOT NULL DEFAULT 0"}
            for name, definition in additions.items():
                if name not in existing_columns:
                    connection.execute(f"ALTER TABLE hayavega_users ADD COLUMN {name} {definition}")
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS hayavega_sessions (
                id {user_id_definition(connection.engine)},
                user_id INTEGER NOT NULL REFERENCES hayavega_users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        if connection.engine == "sqlite3":
            existing_session_columns = {row["name"] for row in connection.execute("PRAGMA table_info(hayavega_sessions)")}
            for name, definition in {"ip_address": "TEXT", "user_agent": "TEXT"}.items():
                if name not in existing_session_columns:
                    connection.execute(f"ALTER TABLE hayavega_sessions ADD COLUMN {name} {definition}")
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS hayavega_auth_tokens (
                id {user_id_definition(connection.engine)},
                user_id INTEGER NOT NULL REFERENCES hayavega_users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                consumed_at INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS hayavega_groups (
                id {user_id_definition(connection.engine)},
                name TEXT NOT NULL UNIQUE,
                permissions TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS hayavega_user_groups (
                id {user_id_definition(connection.engine)},
                user_id INTEGER NOT NULL REFERENCES hayavega_users(id) ON DELETE CASCADE,
                group_id INTEGER NOT NULL REFERENCES hayavega_groups(id) ON DELETE CASCADE
            )
            """
        )
        connection.commit()
    finally:
        connection.close()
    _INITIALIZED_AUTH_DBS.add(key)


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    requirements = (any(char.islower() for char in password), any(char.isupper() for char in password), any(char.isdigit() for char in password), any(not char.isalnum() for char in password))
    if not all(requirements):
        raise ValueError("Password must contain lowercase, uppercase, number, and symbol characters.")


def min_length_validator(length: int) -> Callable[[str], None]:
    """Return a validator rejecting any password shorter than `length`
    -- a relaxed drop-in replacement for the default `validate_password`
    when a project wants a different length floor without its
    character-class requirements."""

    def validator(password: str) -> None:
        if len(password) < length:
            raise ValueError(f"Password must contain at least {length} characters.")

    return validator


def common_password_validator(denylist: Iterable[str]) -> Callable[[str], None]:
    """Return a validator rejecting any password (case-insensitively)
    found in `denylist`. Pair with `validate_password`/`min_length_validator`
    rather than using alone -- this alone enforces no length or
    complexity requirement."""
    denied = {entry.lower() for entry in denylist}

    def validator(password: str) -> None:
        if password.lower() in denied:
            raise ValueError("This password is too common.")

    return validator


def _run_validators(password: str, validators: Iterable[Callable[[str], None]] | None) -> None:
    for validator in validators if validators is not None else (validate_password,):
        validator(password)


def hash_password(password: str) -> str:
    """Hash `password` -- does **not** validate it. Called both when a
    user is choosing a new password (validated by the caller first,
    e.g. `create_user`/`reset_password`) and when a successful login
    silently re-hashes an already-*verified* legacy PBKDF2 password
    under scrypt (`authenticate`) -- that second case must never
    re-validate, or a policy tightened after the fact would lock out a
    user whose already-correct password no longer satisfies it.
    """
    salt = os.urandom(16)
    derived_key = scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode("ascii"), base64.b64encode(derived_key).decode("ascii"))


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        parts = encoded_password.split("$")
        if parts[0] == "scrypt" and len(parts) == 6:
            _, n, r, p, encoded_salt, encoded_key = parts
            derived_key = scrypt(password.encode("utf-8"), salt=base64.b64decode(encoded_salt), n=int(n), r=int(r), p=int(p), dklen=32)
            return compare_digest(base64.b64encode(derived_key).decode("ascii"), encoded_key)
        # Retain verification for legacy Hayavega PBKDF2 records, then upgrade on next login.
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
        row = connection.execute("SELECT * FROM hayavega_users WHERE username = ?", (username,)).fetchone()
        return _user_from_row(row) if row is not None else None
    finally:
        connection.close()


def create_user(database_path: str | Path | dict, username: str, email: str, password: str, *, is_superuser: bool = False, validators: Iterable[Callable[[str], None]] | None = None) -> User:
    """`validators`, when given, replaces the default `[validate_password]`
    entirely -- each is called as `validator(password)` and must raise
    `ValueError` to reject it; the first rejection wins. Omit it for the
    default policy."""
    username, email = username.strip(), email.strip().lower()
    if not username or len(username) > 150:
        raise ValueError("Username is required and must be 150 characters or fewer.")
    if not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError("A valid email address is required.")
    _run_validators(password, validators)
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        insert = "INSERT INTO hayavega_users (username, email, password_hash, is_superuser) VALUES (?, ?, ?, ?)"
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


def create_superuser(database_path: str | Path | dict, username: str, email: str, password: str, *, validators: Iterable[Callable[[str], None]] | None = None) -> User:
    return create_user(database_path, username, email, password, is_superuser=True, validators=validators)


# ---------------------------------------------------------------------------
# Groups & permissions: role-based access narrower than Django's
# Group/Permission models (no contenttypes dependency -- permissions are
# free-form string codenames, e.g. "blog.add_post", stored as a JSON list
# per group) but built on the same idea: group a set of permissions once,
# assign users to groups, check membership at authorization time.
# `security.has_permission_required` is the decorator built on this.
# ---------------------------------------------------------------------------


def _group_from_row(row) -> Group:
    return Group(row["id"], row["name"], tuple(json.loads(row["permissions"] or "[]")))


def create_group(database_path: str | Path | dict, name: str, permissions: Iterable[str] = ()) -> Group:
    initialize_auth_db(database_path)
    codenames = tuple(sorted(set(permissions)))
    connection = _connect(database_path)
    try:
        insert = "INSERT INTO hayavega_groups (name, permissions) VALUES (?, ?)"
        cursor = connection.execute(
            insert + (" RETURNING id" if connection.engine == "postgresql" else ""),
            (name, json.dumps(codenames)),
        )
        connection.commit()
        identifier = cursor.fetchone()["id"] if connection.engine == "postgresql" else cursor.lastrowid
        return Group(identifier, name, codenames)
    except sqlite3.IntegrityError as error:
        raise ValueError(f"A group named {name!r} already exists.") from error
    finally:
        connection.close()


def get_group_by_name(database_path: str | Path | dict, name: str) -> Group | None:
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        row = connection.execute("SELECT id, name, permissions FROM hayavega_groups WHERE name = ?", (name,)).fetchone()
        return _group_from_row(row) if row is not None else None
    finally:
        connection.close()


def add_user_to_group(database_path: str | Path | dict, user: User, group: Group) -> None:
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        existing = connection.execute("SELECT 1 FROM hayavega_user_groups WHERE user_id = ? AND group_id = ?", (user.id, group.id)).fetchone()
        if existing is None:
            connection.execute("INSERT INTO hayavega_user_groups (user_id, group_id) VALUES (?, ?)", (user.id, group.id))
            connection.commit()
            record_audit_event(database_path, "auth.user_added_to_group", actor_id=user.id, metadata={"group": group.name})
    finally:
        connection.close()


def remove_user_from_group(database_path: str | Path | dict, user: User, group: Group) -> None:
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        cursor = connection.execute("DELETE FROM hayavega_user_groups WHERE user_id = ? AND group_id = ?", (user.id, group.id))
        connection.commit()
        if cursor.rowcount:
            record_audit_event(database_path, "auth.user_removed_from_group", actor_id=user.id, metadata={"group": group.name})
    finally:
        connection.close()


def user_permissions(database_path: str | Path | dict, user: User) -> set[str]:
    """Return every permission codename granted to `user` through group
    membership -- the union of every group's permissions the user
    belongs to. Does not special-case `is_superuser`: callers that want
    "superuser bypasses every permission check" (the same default every
    other predicate in this codebase already applies -- see
    `security.superuser_required`/`has_permission_required`) check
    `user.is_superuser` themselves.
    """
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        rows = connection.execute(
            "SELECT g.permissions FROM hayavega_groups g JOIN hayavega_user_groups ug ON ug.group_id = g.id WHERE ug.user_id = ?",
            (user.id,),
        ).fetchall()
        permissions: set[str] = set()
        for row in rows:
            permissions.update(json.loads(row["permissions"] or "[]"))
        return permissions
    finally:
        connection.close()


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_code(secret: str, timestamp: int) -> str:
    normalized = secret.upper() + "=" * (-len(secret) % 8)
    counter = timestamp // 30
    digest = new(base64.b32decode(normalized), struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret: str, code: str, timestamp: int | None = None) -> bool:
    now = int(time.time()) if timestamp is None else timestamp
    return any(compare_digest(_totp_code(secret, now + step * 30), str(code)) for step in (-1, 0, 1))


def enable_totp(database_path: str | Path | dict, username: str, *, secret_key: str | None = None) -> str:
    """Generate a TOTP secret, store it, and return the plaintext secret
    (for the admin to hand to the user's authenticator app -- never the
    stored value, which is encrypted when `secret_key` is given and
    `cryptography` is installed; see `crypto.py`). Without `secret_key`,
    or without `cryptography` installed, the secret is stored in plaintext
    -- `hayavega diagnose` reports that fallback.
    """
    initialize_auth_db(database_path)
    secret = generate_totp_secret()
    stored_secret = secret
    if secret_key:
        try:
            stored_secret = crypto.encrypt(secret_key, secret)
        except crypto.EncryptionUnavailableError:
            pass
    connection = _connect(database_path)
    try:
        cursor = connection.execute("UPDATE hayavega_users SET totp_secret = ?, totp_enabled = 1 WHERE username = ?", (stored_secret, username))
        connection.commit()
        if cursor.rowcount != 1:
            raise ValueError("User was not found.")
        return secret
    finally:
        connection.close()


def _record_failure(connection, row, now: int) -> None:
    attempts = row["failed_attempts"] + 1
    locked_until = now + LOCKOUT_SECONDS if attempts >= MAX_FAILED_ATTEMPTS else None
    connection.execute("UPDATE hayavega_users SET failed_attempts = ?, locked_until = ? WHERE id = ?", (attempts, locked_until, row["id"]))
    connection.commit()


def _login_context(ip_address: str | None, user_agent: str | None) -> dict:
    """Non-sensitive login telemetry: an IP address and a user-agent string, never a header dump."""
    context = {}
    if ip_address:
        context["ip_address"] = ip_address
    if user_agent:
        context["user_agent"] = user_agent
    return context


def _resolve_totp_secret(stored_secret: str | None, secret_key: str | None) -> str | None:
    """Return the plaintext TOTP secret, or None if it can't be resolved
    (encrypted but no/wrong `secret_key`, or `cryptography` unavailable) --
    callers must treat None the same as "verification cannot succeed", not
    silently skip the check."""
    if not stored_secret:
        return None
    if not crypto.is_encrypted(stored_secret):
        return stored_secret
    if not secret_key:
        return None
    try:
        return crypto.decrypt(secret_key, stored_secret)
    except (crypto.EncryptionUnavailableError, ValueError):
        return None


def authenticate(database_path: str | Path | dict, username: str, password: str, totp_code: str | None = None, *, ip_address: str | None = None, user_agent: str | None = None, secret_key: str | None = None) -> User | None:
    """Verify credentials and record the attempt, including the caller-supplied IP address and user agent, if given.

    `secret_key` is only needed when the user has TOTP enabled and their
    secret was stored encrypted (see `enable_totp`) -- pass the same
    `Settings.secret_key` used to enable it, or the TOTP check fails
    closed rather than silently skipping it.
    """
    initialize_auth_db(database_path)
    now = int(time.time())
    context = _login_context(ip_address, user_agent)
    connection = _connect(database_path)
    try:
        row = connection.execute("SELECT * FROM hayavega_users WHERE username = ?", (username,)).fetchone()
        if row is None or not row["is_active"]:
            record_audit_event(database_path, "auth.login_failed", metadata={"reason": "invalid_credentials", **context})
            return None
        if row["locked_until"] and row["locked_until"] > now:
            record_audit_event(database_path, "auth.login_failed", actor_id=row["id"], metadata={"reason": "locked", **context})
            return None
        password_valid = verify_password(password, row["password_hash"])
        totp_secret = _resolve_totp_secret(row["totp_secret"], secret_key) if row["totp_enabled"] else None
        totp_valid = not row["totp_enabled"] or (totp_code is not None and totp_secret is not None and verify_totp(totp_secret, totp_code, now))
        if not password_valid or not totp_valid:
            _record_failure(connection, row, now)
            record_audit_event(database_path, "auth.login_failed", actor_id=row["id"], metadata={"reason": "invalid_credentials", **context})
            return None
        connection.execute("UPDATE hayavega_users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (row["id"],))
        if row["password_hash"].startswith("pbkdf2_sha256$"):
            connection.execute("UPDATE hayavega_users SET password_hash = ? WHERE id = ?", (hash_password(password), row["id"]))
        connection.commit()
        user = _user_from_row(row)
        record_audit_event(database_path, "auth.login_succeeded", actor_id=user.id, metadata=context)
        return user
    finally:
        connection.close()


def create_session(database_path: str | Path | dict, user: User, duration_seconds: int = DEFAULT_SESSION_SECONDS, *, ip_address: str | None = None, user_agent: str | None = None) -> str:
    if not user.is_active:
        raise ValueError("Cannot create a session for an inactive user.")
    initialize_auth_db(database_path)
    token = secrets.token_urlsafe(48)
    connection = _connect(database_path)
    try:
        connection.execute(
            "INSERT INTO hayavega_sessions (user_id, token_hash, expires_at, ip_address, user_agent) VALUES (?, ?, ?, ?, ?)",
            (user.id, sha256(token.encode()).hexdigest(), int(time.time()) + duration_seconds, ip_address, user_agent),
        )
        connection.commit()
        record_audit_event(database_path, "auth.session_created", actor_id=user.id, metadata=_login_context(ip_address, user_agent))
        return token
    finally:
        connection.close()


def authenticate_session(database_path: str | Path | dict, token: str) -> User | None:
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        now = int(time.time())
        _sweep_expired_sessions_if_due(connection, database_path, now)
        row = connection.execute(
            "SELECT u.* FROM hayavega_sessions s JOIN hayavega_users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
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
        row = connection.execute("SELECT user_id FROM hayavega_sessions WHERE token_hash = ?", (sha256(token.encode()).hexdigest(),)).fetchone()
        connection.execute("DELETE FROM hayavega_sessions WHERE token_hash = ?", (sha256(token.encode()).hexdigest(),))
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
        _sweep_expired_sessions_if_due(connection, database_path, now)
        connection.commit()
        rows = connection.execute("SELECT id, expires_at, created_at, ip_address, user_agent FROM hayavega_sessions WHERE user_id = ? ORDER BY created_at DESC", (user.id,)).fetchall()
        return [SessionSummary(row["id"], row["created_at"], row["expires_at"], row["ip_address"], row["user_agent"]) for row in rows]
    finally:
        connection.close()


def revoke_all_sessions(database_path: str | Path | dict, user: User, *, except_token: str | None = None) -> int:
    """Revoke every session for a user, optionally keeping one (the caller's current token)."""
    initialize_auth_db(database_path)
    connection = _connect(database_path)
    try:
        if except_token:
            cursor = connection.execute(
                "DELETE FROM hayavega_sessions WHERE user_id = ? AND token_hash != ?",
                (user.id, sha256(except_token.encode()).hexdigest()),
            )
        else:
            cursor = connection.execute("DELETE FROM hayavega_sessions WHERE user_id = ?", (user.id,))
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
        row = connection.execute("SELECT user_id FROM hayavega_sessions WHERE token_hash = ? AND expires_at > ?", (old_hash, now)).fetchone()
        if row is None:
            return None
        new_token = secrets.token_urlsafe(48)
        connection.execute(
            "UPDATE hayavega_sessions SET token_hash = ?, expires_at = ? WHERE token_hash = ?",
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
            "INSERT INTO hayavega_auth_tokens (user_id, purpose, token_hash, expires_at) VALUES (?, ?, ?, ?)",
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
            "SELECT t.id AS token_id, u.* FROM hayavega_auth_tokens t JOIN hayavega_users u ON u.id = t.user_id WHERE t.token_hash = ? AND t.purpose = ? AND t.consumed_at IS NULL AND t.expires_at > ?",
            (sha256(token.encode()).hexdigest(), purpose, now),
        ).fetchone()
        if row is None:
            return None
        connection.execute("UPDATE hayavega_auth_tokens SET consumed_at = ? WHERE id = ?", (now, row["token_id"]))
        connection.commit()
        return row
    finally:
        connection.close()


def create_password_reset_token(database_path: str | Path | dict, user: User) -> str:
    token = _issue_auth_token(database_path, user, _PASSWORD_RESET_PURPOSE, PASSWORD_RESET_TOKEN_SECONDS)
    record_audit_event(database_path, "auth.password_reset_requested", actor_id=user.id)
    return token


def reset_password(database_path: str | Path | dict, token: str, new_password: str, *, validators: Iterable[Callable[[str], None]] | None = None) -> User | None:
    """Consume a password-reset token, set the new password, and revoke every existing session.

    Validates `new_password` (see `create_user`'s `validators`) *before*
    consuming the token -- a rejected password must never burn a
    single-use reset link the user would then have to request again.
    """
    _run_validators(new_password, validators)
    row = _consume_auth_token(database_path, token, _PASSWORD_RESET_PURPOSE)
    if row is None:
        return None
    connection = _connect(database_path)
    try:
        connection.execute(
            "UPDATE hayavega_users SET password_hash = ?, failed_attempts = 0, locked_until = NULL WHERE id = ?",
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
        connection.execute("UPDATE hayavega_users SET email_verified = 1 WHERE id = ?", (row["id"],))
        connection.commit()
    finally:
        connection.close()
    record_audit_event(database_path, "auth.email_verified", actor_id=row["id"])
    return User(row["id"], row["username"], row["email"], bool(row["is_superuser"]), bool(row["is_active"]), bool(row["totp_enabled"]), True)
