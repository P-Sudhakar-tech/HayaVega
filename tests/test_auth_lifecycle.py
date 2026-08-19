import sqlite3
import tempfile
import unittest
from pathlib import Path

from mangoo.auth import (
    authenticate,
    authenticate_session,
    create_email_verification_token,
    create_password_reset_token,
    create_session,
    create_superuser,
    get_user_by_username,
    list_sessions,
    reset_password,
    revoke_all_sessions,
    rotate_session,
    verify_email,
)
from mangoo.audit import audit_events


PASSWORD = "Correct-Horse-7!"
NEW_PASSWORD = "Even-Better-9?"


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        self.user = create_superuser(self.database, "admin", "admin@example.com", PASSWORD)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_reset_changes_password_and_revokes_every_session(self):
        old_token = create_session(self.database, self.user)
        token = create_password_reset_token(self.database, self.user)

        reset_user = reset_password(self.database, token, NEW_PASSWORD)

        self.assertEqual(reset_user.id, self.user.id)
        self.assertIsNone(authenticate(self.database, "admin", PASSWORD))
        self.assertIsNotNone(authenticate(self.database, "admin", NEW_PASSWORD))
        self.assertIsNone(authenticate_session(self.database, old_token))

    def test_token_is_single_use(self):
        token = create_password_reset_token(self.database, self.user)
        self.assertIsNotNone(reset_password(self.database, token, NEW_PASSWORD))
        self.assertIsNone(reset_password(self.database, token, "Another-Password-1!"))

    def test_expired_token_is_rejected(self):
        token = create_password_reset_token(self.database, self.user)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE mangoo_auth_tokens SET expires_at = 0")
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(reset_password(self.database, token, NEW_PASSWORD))

    def test_unknown_token_is_rejected(self):
        self.assertIsNone(reset_password(self.database, "not-a-real-token", NEW_PASSWORD))

    def test_weak_replacement_password_is_rejected(self):
        token = create_password_reset_token(self.database, self.user)
        with self.assertRaisesRegex(ValueError, "at least 12"):
            reset_password(self.database, token, "short")

    def test_lifecycle_is_audited_without_leaking_the_token(self):
        token = create_password_reset_token(self.database, self.user)
        reset_password(self.database, token, NEW_PASSWORD)
        events = [event["event_type"] for event in audit_events(self.database)]
        self.assertIn("auth.password_reset_requested", events)
        self.assertIn("auth.password_reset_completed", events)
        self.assertNotIn(token, str(audit_events(self.database)))


class EmailVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        self.user = create_superuser(self.database, "admin", "admin@example.com", PASSWORD)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_new_accounts_start_unverified(self):
        self.assertFalse(self.user.email_verified)
        self.assertFalse(get_user_by_username(self.database, "admin").email_verified)

    def test_verification_marks_the_account_verified(self):
        token = create_email_verification_token(self.database, self.user)
        verified_user = verify_email(self.database, token)
        self.assertTrue(verified_user.email_verified)
        self.assertTrue(get_user_by_username(self.database, "admin").email_verified)

    def test_token_is_single_use(self):
        token = create_email_verification_token(self.database, self.user)
        self.assertIsNotNone(verify_email(self.database, token))
        self.assertIsNone(verify_email(self.database, token))

    def test_expired_token_is_rejected(self):
        token = create_email_verification_token(self.database, self.user)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE mangoo_auth_tokens SET expires_at = 0")
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(verify_email(self.database, token))
        self.assertFalse(get_user_by_username(self.database, "admin").email_verified)

    def test_lifecycle_is_audited(self):
        token = create_email_verification_token(self.database, self.user)
        verify_email(self.database, token)
        events = [event["event_type"] for event in audit_events(self.database)]
        self.assertIn("auth.email_verification_requested", events)
        self.assertIn("auth.email_verified", events)


class SessionManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        self.user = create_superuser(self.database, "admin", "admin@example.com", PASSWORD)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_list_sessions_reflects_active_sessions_without_exposing_tokens(self):
        create_session(self.database, self.user)
        create_session(self.database, self.user)
        sessions = list_sessions(self.database, self.user)
        self.assertEqual(len(sessions), 2)
        for session in sessions:
            for field in ("id", "created_at", "expires_at"):
                self.assertTrue(hasattr(session, field))
            self.assertFalse(hasattr(session, "token") or hasattr(session, "token_hash"))

    def test_revoke_all_sessions_can_keep_one_token(self):
        kept = create_session(self.database, self.user)
        revoked = create_session(self.database, self.user)

        count = revoke_all_sessions(self.database, self.user, except_token=kept)

        self.assertEqual(count, 1)
        self.assertIsNotNone(authenticate_session(self.database, kept))
        self.assertIsNone(authenticate_session(self.database, revoked))

    def test_revoke_all_sessions_without_exception_clears_everything(self):
        create_session(self.database, self.user)
        create_session(self.database, self.user)
        count = revoke_all_sessions(self.database, self.user)
        self.assertEqual(count, 2)
        self.assertEqual(list_sessions(self.database, self.user), [])

    def test_rotate_session_issues_a_new_token_and_invalidates_the_old_one(self):
        old_token = create_session(self.database, self.user)

        new_token = rotate_session(self.database, old_token)

        self.assertIsNotNone(new_token)
        self.assertNotEqual(new_token, old_token)
        self.assertIsNone(authenticate_session(self.database, old_token))
        rotated_user = authenticate_session(self.database, new_token)
        self.assertIsNotNone(rotated_user)
        self.assertEqual(rotated_user.id, self.user.id)

    def test_rotate_session_rejects_an_unknown_token(self):
        self.assertIsNone(rotate_session(self.database, "not-a-real-token"))
