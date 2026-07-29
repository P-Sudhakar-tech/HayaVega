import json
import tempfile
import unittest
from pathlib import Path

from mangoo import admin_routes, create_session, create_superuser, create_user, csrf_protect, login_required
from mangoo.request import Request
from mangoo.security import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, cookie, csrf_token_response


def request(method="GET", *, headers=None):
    return Request(method, "/", {}, b"", headers or {})


class ProductionFeatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "users.sqlite3"
        self.user = create_user(self.database, "member", "member@example.com", "Strong-Password-7!")
        self.admin = create_superuser(self.database, "admin", "admin@example.com", "Strong-Password-7!")

    def tearDown(self):
        self.directory.cleanup()

    def test_csrf_endpoint_sets_token_cookie(self):
        response = csrf_token_response(request())
        token = json.loads(response.body)["csrf_token"]
        self.assertTrue(token)
        self.assertIn(f"{CSRF_COOKIE_NAME}={token}", dict(response.headers)["Set-Cookie"])

    def test_secure_cookie_mode_adds_secure_attribute(self):
        self.assertIn("Secure", cookie("session", "token", secure=True)[1])

    def test_csrf_protect_requires_matching_header_for_cookie_session(self):
        protected = csrf_protect(lambda request: {"ok": True})
        missing = protected(request("POST", headers={"Cookie": f"{SESSION_COOKIE_NAME}=token; {CSRF_COOKIE_NAME}=csrf"}))
        allowed = protected(request("POST", headers={"Cookie": f"{SESSION_COOKIE_NAME}=token; {CSRF_COOKIE_NAME}=csrf", "X-Csrf-Token": "csrf"}))
        self.assertEqual(missing.status, 403)
        self.assertEqual(allowed, {"ok": True})

    def test_login_required_accepts_cookie_session(self):
        token = create_session(self.database, self.user)
        protected = login_required(self.database)(lambda request: {"ok": True})
        response = protected(request(headers={"Cookie": f"{SESSION_COOKIE_NAME}={token}"}))
        self.assertEqual(response, {"ok": True})

    def test_admin_base_requires_admin_and_lists_users(self):
        routes = {route.pattern: route for route in admin_routes(self.database)}
        member_token = create_session(self.database, self.user)
        admin_token = create_session(self.database, self.admin)
        denied = routes["admin"].view(request(headers={"Authorization": f"Bearer {member_token}"}))
        page = routes["admin/users"].view(request(headers={"Authorization": f"Bearer {admin_token}"}))
        self.assertEqual(denied.status, 403)
        self.assertIn("member@example.com", page.body)
        self.assertIn("admin@example.com", page.body)
