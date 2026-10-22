import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mangoo import auth as auth_module
from mangoo import crypto
from mangoo.audit import audit_events
from mangoo.auth import (
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
            connection.execute("UPDATE mangoo_users SET is_active = 0 WHERE username = 'admin'")
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
            return connection.execute("SELECT totp_secret FROM mangoo_users WHERE username = 'admin'").fetchone()[0]
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

    @patch("mangoo.auth.crypto.encrypt")
    def test_totp_secret_falls_back_to_plaintext_when_cryptography_is_unavailable(self, encrypt):
        encrypt.side_effect = crypto.EncryptionUnavailableError("cryptography is not installed")
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        secret = enable_totp(self.database, "admin", secret_key="a-real-unique-secret")
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
        with patch("mangoo.auth._connect", wraps=auth_module._connect) as connect_spy:
            initialize_auth_db(database)
            initialize_auth_db(database)
        self.assertEqual(connect_spy.call_count, 1)

    def test_a_different_database_gets_its_own_independent_cache_entry(self):
        first = Path(self.temporary_directory.name) / "first.sqlite3"
        second = Path(self.temporary_directory.name) / "second.sqlite3"
        with patch("mangoo.auth._connect", wraps=auth_module._connect) as connect_spy:
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
