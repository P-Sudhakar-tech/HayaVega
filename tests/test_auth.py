import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from mangoo.auth import (
    UserAlreadyExistsError,
    _totp_code,
    authenticate,
    authenticate_session,
    create_session,
    create_superuser,
    enable_totp,
    hash_password,
    revoke_session,
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

    def test_session_tokens_are_hashed_and_can_be_revoked(self):
        user = create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        token = create_session(self.database, user)
        self.assertIsNotNone(authenticate_session(self.database, token))
        revoke_session(self.database, token)
        self.assertIsNone(authenticate_session(self.database, token))

    def test_repeated_failed_logins_lock_the_account(self):
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        for _ in range(5):
            self.assertIsNone(authenticate(self.database, "admin", "Wrong-Password-7!"))
        self.assertIsNone(authenticate(self.database, "admin", PASSWORD))
