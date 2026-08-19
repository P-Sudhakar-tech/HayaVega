import tempfile
import unittest
from pathlib import Path

from mangoo.auth import create_superuser
from mangoo.auth_api import auth_routes
from mangoo.response import json_response
from mangoo.routing import path
from mangoo.runtime import create_application
from mangoo.security import email_verified_required
from mangoo.testing import TestClient


PASSWORD = "Correct-Horse-7!"
NEW_PASSWORD = "Even-Better-9?"


class CapturingNotifier:
    """Stand-in for a real mail provider; captures the token instead of sending it."""

    def __init__(self):
        self.sent: list[tuple[str, int, str]] = []

    def __call__(self, kind, user, token):
        self.sent.append((kind, user.id, token))

    @property
    def last_token(self) -> str:
        return self.sent[-1][2]


class AuthLifecycleApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        self.notifier = CapturingNotifier()
        self.client = TestClient(create_application(auth_routes(self.database, notify=self.notifier)))

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_password_reset_request_does_not_reveal_whether_the_username_exists(self):
        known = self.client.post("/api/auth/password-reset/request", json_data={"username": "admin"})
        unknown = self.client.post("/api/auth/password-reset/request", json_data={"username": "nobody"})
        self.assertEqual(known.status_code, 202)
        self.assertEqual(unknown.status_code, 202)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertEqual(self.notifier.sent[0][0], "password_reset")

    def test_password_reset_confirm_flow_lets_the_user_log_in_with_the_new_password(self):
        self.client.post("/api/auth/password-reset/request", json_data={"username": "admin"})
        token = self.notifier.last_token

        confirm = self.client.post("/api/auth/password-reset/confirm", json_data={"token": token, "password": NEW_PASSWORD})
        self.assertEqual(confirm.status_code, 204)

        old_login = self.client.post("/api/auth/login", json_data={"username": "admin", "password": PASSWORD})
        self.assertEqual(old_login.status_code, 401)
        new_login = self.client.post("/api/auth/login", json_data={"username": "admin", "password": NEW_PASSWORD})
        self.assertEqual(new_login.status_code, 200)

    def test_password_reset_confirm_rejects_an_invalid_token(self):
        response = self.client.post("/api/auth/password-reset/confirm", json_data={"token": "not-a-real-token", "password": NEW_PASSWORD})
        self.assertEqual(response.status_code, 400)

    def test_email_verification_requires_authentication(self):
        response = self.client.post("/api/auth/email/verify/request")
        self.assertEqual(response.status_code, 401)

    def test_email_verification_flow_updates_the_current_user(self):
        login = self.client.post("/api/auth/login", json_data={"username": "admin", "password": PASSWORD})
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        before = self.client.get("/api/auth/me", headers=headers)
        self.assertFalse(before.json()["email_verified"])

        self.client.post("/api/auth/email/verify/request", headers=headers)
        confirm = self.client.post("/api/auth/email/verify/confirm", json_data={"token": self.notifier.last_token})
        self.assertEqual(confirm.status_code, 204)

        after = self.client.get("/api/auth/me", headers=headers)
        self.assertTrue(after.json()["email_verified"])

    def test_sessions_are_listed_and_others_can_be_revoked(self):
        first = self.client.post("/api/auth/login", json_data={"username": "admin", "password": PASSWORD}).json()["token"]
        second = self.client.post("/api/auth/login", json_data={"username": "admin", "password": PASSWORD}).json()["token"]
        self.client.cookies.clear()

        listing = self.client.get("/api/auth/sessions", headers={"Authorization": f"Bearer {first}"})
        self.assertEqual(len(listing.json()), 2)

        revoke = self.client.post("/api/auth/sessions/revoke-others", headers={"Authorization": f"Bearer {first}"})
        self.assertEqual(revoke.status_code, 200)
        self.assertEqual(revoke.json()["revoked"], 1)

        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {first}"}).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {second}"}).status_code, 401)

    def test_email_verified_required_gates_a_view_until_verified(self):
        @email_verified_required(self.database)
        def members_only(request):
            return json_response({"ok": True})

        client = TestClient(create_application([path("members", members_only), *auth_routes(self.database, notify=self.notifier)]))
        token = client.post("/api/auth/login", json_data={"username": "admin", "password": PASSWORD}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        self.assertEqual(client.get("/members", headers=headers).status_code, 403)

        client.post("/api/auth/email/verify/request", headers=headers)
        client.post("/api/auth/email/verify/confirm", json_data={"token": self.notifier.last_token})

        self.assertEqual(client.get("/members", headers=headers).status_code, 200)
