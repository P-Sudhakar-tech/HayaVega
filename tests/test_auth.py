import base64
import hashlib
import os
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hayavega import auth as auth_module
from hayavega import crypto
from hayavega.audit import audit_events
from hayavega.auth import (
    UserAlreadyExistsError,
    _totp_code,
    authenticate,
    authenticate_session,
    common_password_validator,
    create_password_reset_token,
    create_session,
    create_superuser,
    create_user,
    enable_totp,
    hash_password,
    initialize_auth_db,
    min_length_validator,
    reset_password,
    revoke_session,
    validate_password,
    verify_password,
)

PASSWORD = "Correct-Horse-7!"


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_password_uses_memory_hard_scrypt_hashing(self):
        encoded = hash_password(PASSWORD)
        self.assertTrue(encoded.startswith("scrypt$"))
        self.assertNotIn(PASSWORD, encoded)
        self.assertTrue(verify_password(PASSWORD, encoded))
        self.assertFalse(verify_password("incorrect-password", encoded))

    def test_verify_password_accepts_a_legacy_pbkdf2_hash(self):
        """Pre-scrypt Hayavega records are `pbkdf2_sha256$<iterations>$<salt>$<key>`
        -- `verify_password` must still check these so a legacy user can log
        in at all (before `authenticate` upgrades the hash to scrypt)."""
        iterations = 100_000
        salt = os.urandom(16)
        derived_key = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode("utf-8"), salt, iterations)
        encoded = "pbkdf2_sha256${}${}${}".format(iterations, base64.b64encode(salt).decode("ascii"), base64.b64encode(derived_key).decode("ascii"))
        self.assertTrue(verify_password(PASSWORD, encoded))
        self.assertFalse(verify_password("wrong-password", encoded))

    def test_verify_password_treats_a_malformed_pbkdf2_hash_as_invalid_not_an_error(self):
        self.assertFalse(verify_password(PASSWORD, "pbkdf2_sha256$not-an-integer$c2FsdA==$a2V5"))

    def test_create_superuser_and_authenticate(self):
        created = create_superuser(self.database, "admin", "Admin@Example.com", PASSWORD)
        authenticated = authenticate(self.database, "admin", PASSWORD)
        self.assertTrue(created.is_superuser)
        self.assertIsNotNone(authenticated)
        self.assertTrue(authenticated.is_superuser)
        self.assertEqual(authenticated.email, "admin@example.com")

    def test_duplicate_username_or_email_is_rejected(self):
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        with self.assertRaises(UserAlreadyExistsError):
            create_superuser(self.database, "admin", "other@example.com", PASSWORD)
        with self.assertRaises(UserAlreadyExistsError):
            create_superuser(self.database, "other", "admin@example.com", PASSWORD)

    def test_password_policy_and_invalid_email_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least 12"):
            create_superuser(self.database, "admin", "admin@example.com", "short")
        with self.assertRaisesRegex(ValueError, "lowercase"):
            create_superuser(self.database, "admin", "admin@example.com", "alllowercase12")
        with self.assertRaisesRegex(ValueError, "valid email"):
            create_superuser(self.database, "admin", "not-an-email", PASSWORD)

    def test_inactive_user_cannot_authenticate(self):
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE hayavega_users SET is_active = 0 WHERE username = 'admin'")
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(authenticate(self.database, "admin", PASSWORD))

    def test_two_factor_authentication_requires_totp_code(self):
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        secret = enable_totp(self.database, "admin")
        self.assertIsNone(authenticate(self.database, "admin", PASSWORD))
        code = _totp_code(secret, int(time.time()))
        self.assertIsNotNone(authenticate(self.database, "admin", PASSWORD, code))

    def _raw_totp_secret(self) -> str:
        connection = sqlite3.connect(self.database)
        try:
            return connection.execute("SELECT totp_secret FROM hayavega_users WHERE username = 'admin'").fetchone()[0]
        finally:
            connection.close()

    def test_totp_secret_is_encrypted_at_rest_when_a_secret_key_is_given(self):
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        secret = enable_totp(self.database, "admin", secret_key="a-real-unique-secret")
        stored = self._raw_totp_secret()
        self.assertTrue(crypto.is_encrypted(stored))
        self.assertNotIn(secret, stored)
        code = _totp_code(secret, int(time.time()))
        self.assertIsNotNone(authenticate(self.database, "admin", PASSWORD, code, secret_key="a-real-unique-secret"))

    def test_totp_authentication_fails_closed_without_the_secret_key(self):
        """An encrypted secret that can't be decrypted must count as a
        failed check, never a skipped one -- silently accepting any code
        because decryption failed would make TOTP meaningless."""
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        secret = enable_totp(self.database, "admin", secret_key="a-real-unique-secret")
        code = _totp_code(secret, int(time.time()))
        self.assertIsNone(authenticate(self.database, "admin", PASSWORD, code))

    def test_totp_authentication_fails_closed_with_the_wrong_secret_key(self):
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        secret = enable_totp(self.database, "admin", secret_key="a-real-unique-secret")
        code = _totp_code(secret, int(time.time()))
        self.assertIsNone(authenticate(self.database, "admin", PASSWORD, code, secret_key="a-different-secret"))

    @patch("hayavega.auth.crypto.encrypt")
    def test_enable_totp_raises_instead_of_silently_storing_plaintext_when_a_secret_key_was_given(self, encrypt):
        """Passing `secret_key=` is an explicit request to encrypt the stored
        secret; silently falling back to plaintext when `cryptography` turns
        out to be unavailable would downgrade that request without telling
        the caller -- it must fail loudly instead, the same standard this
        project applies to its other security-relevant fallbacks."""
        encrypt.side_effect = crypto.EncryptionUnavailableError("cryptography is not installed")
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        with self.assertRaises(crypto.EncryptionUnavailableError):
            enable_totp(self.database, "admin", secret_key="a-real-unique-secret")

    def test_enable_totp_still_stores_plaintext_when_no_secret_key_is_given_at_all(self):
        """Omitting `secret_key` entirely is a deliberate opt-out (reported
        by `hayavega diagnose`), unlike the case above -- that path is
        unchanged."""
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        secret = enable_totp(self.database, "admin")
        self.assertEqual(self._raw_totp_secret(), secret)
        code = _totp_code(secret, int(time.time()))
        self.assertIsNotNone(authenticate(self.database, "admin", PASSWORD, code))

    def test_session_tokens_are_hashed_and_can_be_revoked(self):
        user = create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        token = create_session(self.database, user)
        self.assertIsNotNone(authenticate_session(self.database, token))
        revoke_session(self.database, token)
        self.assertIsNone(authenticate_session(self.database, token))

    def test_authentication_events_are_persisted_without_credentials(self):
        user = create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        self.assertIsNone(authenticate(self.database, "admin", "Wrong-Password-7!"))
        token = create_session(self.database, user)
        revoke_session(self.database, token)
        events = audit_events(self.database)
        self.assertEqual([event["event_type"] for event in events[:4]], ["auth.session_revoked", "auth.session_created", "auth.login_failed", "auth.user_created"])
        self.assertNotIn("Wrong-Password-7!", str(events))

    def test_repeated_failed_logins_lock_the_account(self):
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        for _ in range(5):
            self.assertIsNone(authenticate(self.database, "admin", "Wrong-Password-7!"))
        self.assertIsNone(authenticate(self.database, "admin", PASSWORD))

    def test_a_successful_login_upgrades_a_legacy_pbkdf2_hash_to_scrypt(self):
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        iterations = 100_000
        salt = os.urandom(16)
        derived_key = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode("utf-8"), salt, iterations)
        legacy_hash = "pbkdf2_sha256${}${}${}".format(iterations, base64.b64encode(salt).decode("ascii"), base64.b64encode(derived_key).decode("ascii"))
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE hayavega_users SET password_hash = ? WHERE username = 'admin'", (legacy_hash,))
            connection.commit()
        finally:
            connection.close()

        self.assertIsNotNone(authenticate(self.database, "admin", PASSWORD))

        connection = sqlite3.connect(self.database)
        try:
            stored = connection.execute("SELECT password_hash FROM hayavega_users WHERE username = 'admin'").fetchone()[0]
        finally:
            connection.close()
        self.assertTrue(stored.startswith("scrypt$"))

    def test_enable_totp_raises_when_the_username_does_not_exist(self):
        initialize_auth_db(self.database)
        with self.assertRaisesRegex(ValueError, "not found"):
            enable_totp(self.database, "nobody")

    def test_create_session_rejects_an_inactive_user(self):
        user = create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        inactive_user = replace(user, is_active=False)
        with self.assertRaisesRegex(ValueError, "inactive"):
            create_session(self.database, inactive_user)


class ResolveTotpSecretTests(unittest.TestCase):
    """`_resolve_totp_secret` is called from `authenticate` only when
    `totp_enabled` is set, but it's a pure function in its own right --
    exercised directly here for its "nothing stored" short-circuit."""

    def test_a_missing_or_empty_stored_secret_resolves_to_none(self):
        self.assertIsNone(auth_module._resolve_totp_secret(None, "a-secret-key"))
        self.assertIsNone(auth_module._resolve_totp_secret("", "a-secret-key"))


class PasswordValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_min_length_validator_rejects_a_password_shorter_than_the_given_length(self):
        with self.assertRaisesRegex(ValueError, "8"):
            min_length_validator(8)("short")

    def test_min_length_validator_accepts_a_long_enough_password(self):
        min_length_validator(8)("long-enough")

    def test_common_password_validator_rejects_a_denylisted_password_case_insensitively(self):
        validator = common_password_validator(["password123"])
        with self.assertRaises(ValueError):
            validator("Password123")

    def test_common_password_validator_accepts_anything_not_on_the_list(self):
        common_password_validator(["password123"])("something-else-entirely")

    def test_create_user_rejects_an_empty_or_too_long_username(self):
        with self.assertRaisesRegex(ValueError, "Username is required"):
            create_user(self.database, "", "alice@example.com", PASSWORD)
        with self.assertRaisesRegex(ValueError, "Username is required"):
            create_user(self.database, "x" * 151, "alice@example.com", PASSWORD)

    def test_create_user_uses_validate_password_by_default(self):
        with self.assertRaisesRegex(ValueError, "at least 12"):
            create_user(self.database, "alice", "alice@example.com", "short")

    def test_create_user_accepts_a_custom_validator_list_instead_of_the_default(self):
        """A password `validate_password` would reject (no symbol, under
        the default's complexity rules) succeeds under a relaxed
        custom policy."""
        user = create_user(self.database, "alice", "alice@example.com", "just-twelve1", validators=[min_length_validator(8)])
        self.assertEqual(user.username, "alice")

    def test_create_user_runs_every_validator_in_the_given_list(self):
        with self.assertRaises(ValueError):
            create_user(self.database, "alice", "alice@example.com", "password123", validators=[min_length_validator(8), common_password_validator(["password123"])])

    def test_create_user_rejects_an_empty_validators_list_instead_of_silently_skipping_all_checks(self):
        """`validators=None` (the default) means 'use the standard policy';
        `validators=[]` is never a deliberate 'skip all password checks'
        feature -- it is indistinguishable from a caller accidentally passing
        an empty list, so it must fail loudly rather than let an empty-string
        password through with zero validation."""
        with self.assertRaises(ValueError):
            create_user(self.database, "alice", "alice@example.com", "", validators=[])

    def test_create_superuser_passes_validators_through(self):
        user = create_superuser(self.database, "root", "root@example.com", "short-pw", validators=[min_length_validator(4)])
        self.assertTrue(user.is_superuser)

    def test_hash_password_itself_does_not_validate(self):
        """`hash_password` is also used to silently re-hash an
        already-verified legacy password on login -- it must never
        reject a password on its own, or a policy tightened after the
        fact would lock out a user whose already-correct password no
        longer satisfies it."""
        hash_password("short")  # would fail validate_password(); must not raise here

    def test_reset_password_validates_before_consuming_the_token(self):
        """A rejected new password must never burn a single-use reset
        token -- the same token must still work on a later, valid
        attempt."""
        user = create_user(self.database, "alice", "alice@example.com", PASSWORD)
        token = create_password_reset_token(self.database, user)
        with self.assertRaises(ValueError):
            reset_password(self.database, token, "short")
        result = reset_password(self.database, token, "Another-Strong-9!")
        self.assertIsNotNone(result)

    def test_reset_password_accepts_a_custom_validator_list(self):
        user = create_user(self.database, "alice", "alice@example.com", PASSWORD)
        token = create_password_reset_token(self.database, user)
        result = reset_password(self.database, token, "short-pw", validators=[min_length_validator(4)])
        self.assertIsNotNone(result)

    def test_validate_password_is_still_directly_usable_on_its_own(self):
        with self.assertRaises(ValueError):
            validate_password("short")
        validate_password(PASSWORD)


class InitializeAuthDbCachingTests(unittest.TestCase):
    """`initialize_auth_db` is called from every auth operation -- a second
    call against an already-initialized database must skip the
    CREATE TABLE/PRAGMA/ALTER TABLE round trip entirely, the same way
    `models.Collection.prepare()`'s `_PREPARED_TABLES` cache already does."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)

    def test_a_second_call_against_the_same_database_skips_the_ddl_round_trip(self):
        database = Path(self.temporary_directory.name) / "users.sqlite3"
        with patch("hayavega.auth._connect", wraps=auth_module._connect) as connect_spy:
            initialize_auth_db(database)
            initialize_auth_db(database)
        self.assertEqual(connect_spy.call_count, 1)

    def test_a_different_database_gets_its_own_independent_cache_entry(self):
        first = Path(self.temporary_directory.name) / "first.sqlite3"
        second = Path(self.temporary_directory.name) / "second.sqlite3"
        with patch("hayavega.auth._connect", wraps=auth_module._connect) as connect_spy:
            initialize_auth_db(first)
            initialize_auth_db(second)
        self.assertEqual(connect_spy.call_count, 2)

    def test_the_database_is_actually_usable_after_the_cached_call(self):
        """The cache must never cause a real caller to skip genuine
        initialization -- create_user (which calls initialize_auth_db
        itself) must still work normally after a prior direct call."""
        database = Path(self.temporary_directory.name) / "users.sqlite3"
        initialize_auth_db(database)
        initialize_auth_db(database)
        user = create_user(database, "alice", "alice@example.com", PASSWORD)
        self.assertEqual(user.username, "alice")


class SchemaBackfillTests(unittest.TestCase):
    """`initialize_auth_db` upgrades a pre-existing SQLite schema in place
    (columns added by later Hayavega releases) via `ALTER TABLE ... ADD COLUMN`,
    instead of assuming every database was created fresh by the current
    schema."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"

    def test_a_users_table_missing_newer_columns_is_backfilled(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                """
                CREATE TABLE hayavega_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_superuser INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        initialize_auth_db(self.database)

        connection = sqlite3.connect(self.database)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(hayavega_users)")}
        finally:
            connection.close()
        self.assertTrue({"failed_attempts", "locked_until", "totp_secret", "totp_enabled", "email_verified"} <= columns)

        user = create_user(self.database, "alice", "alice@example.com", PASSWORD)
        self.assertEqual(user.username, "alice")

    def test_a_sessions_table_missing_newer_columns_is_backfilled(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                """
                CREATE TABLE hayavega_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            connection.execute(
                """
                CREATE TABLE hayavega_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES hayavega_users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        initialize_auth_db(self.database)

        connection = sqlite3.connect(self.database)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(hayavega_sessions)")}
        finally:
            connection.close()
        self.assertTrue({"ip_address", "user_agent"} <= columns)

        user = create_user(self.database, "alice", "alice@example.com", PASSWORD)
        token = create_session(self.database, user, ip_address="127.0.0.1", user_agent="pytest")
        self.assertIsNotNone(authenticate_session(self.database, token))


class SessionSweepTests(unittest.TestCase):
    """`authenticate_session`/`list_sessions` no longer delete expired
    `hayavega_sessions` rows on every call -- only at most once per
    `auth_module._SESSION_SWEEP_INTERVAL_SECONDS` per database, since the
    SELECT each one runs already filters on `expires_at > ?` itself; the
    physical delete is housekeeping, not a correctness dependency."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        auth_module._LAST_SESSION_SWEEP.clear()
        self.user = create_user(self.database, "alice", "alice@example.com", PASSWORD)

    def test_an_expired_token_is_rejected_even_when_the_sweep_is_not_due(self):
        token = create_session(self.database, self.user, duration_seconds=-1)
        # The first call ever for this database always sweeps (cold start,
        # nothing recorded yet) -- rejection must not depend on that.
        self.assertIsNone(authenticate_session(self.database, token))
        # A second, immediate call is within the interval -- still rejected.
        self.assertIsNone(authenticate_session(self.database, token))

    def test_the_physical_delete_is_skipped_within_the_interval_and_runs_once_due(self):
        key = auth_module._auth_db_key(self.database)
        # Prime the sweep timestamp so this test's own session isn't swept
        # on its first check below -- isolates "skipped within the interval"
        # from the unconditional cold-start sweep.
        auth_module._LAST_SESSION_SWEEP[key] = time.time()
        token = create_session(self.database, self.user, duration_seconds=-1)
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        authenticate_session(self.database, token)
        connection = auth_module._connect(self.database)
        try:
            still_present = connection.execute("SELECT 1 FROM hayavega_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(still_present, "the row should still physically exist within the sweep interval")

        auth_module._LAST_SESSION_SWEEP[key] = time.time() - auth_module._SESSION_SWEEP_INTERVAL_SECONDS - 1
        authenticate_session(self.database, token)
        connection = auth_module._connect(self.database)
        try:
            gone = connection.execute("SELECT 1 FROM hayavega_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        finally:
            connection.close()
        self.assertIsNone(gone, "the row should be swept once the interval has elapsed")
