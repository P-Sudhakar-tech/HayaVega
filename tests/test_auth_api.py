import io
import json
import tempfile
import unittest
from pathlib import Path

from mangoo.auth import create_superuser
from mangoo.auth_api import auth_routes
from mangoo.runtime import create_application

PASSWORD = "Correct-Horse-7!"


def call(application, path, method="GET", body=None, token=None):
    captured = {}
    encoded_body = json.dumps(body).encode() if body is not None else b""

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_LENGTH": str(len(encoded_body)), "wsgi.input": io.BytesIO(encoded_body)}
    if token:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return captured, json.loads(b"".join(application(environ, start_response))) if path != "/api/auth/logout" else b"".join(application(environ, start_response))


class AuthenticationApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        self.application = create_application(auth_routes(self.database))

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_login_session_lookup_and_logout(self):
        captured, payload = call(self.application, "/api/auth/login", "POST", {"username": "admin", "password": PASSWORD})
        self.assertEqual(captured["status"], "200 OK")
        token = payload["token"]
        _, user = call(self.application, "/api/auth/me", token=token)
        self.assertEqual(user["username"], "admin")
        captured, _ = call(self.application, "/api/auth/logout", "POST", token=token)
        self.assertEqual(captured["status"], "204 No Content")
        captured, _ = call(self.application, "/api/auth/me", token=token)
        self.assertEqual(captured["status"], "401 Unauthorized")

    def test_login_does_not_disclose_whether_username_exists(self):
        captured, payload = call(self.application, "/api/auth/login", "POST", {"username": "missing", "password": PASSWORD})
        self.assertEqual(captured["status"], "401 Unauthorized")
        self.assertEqual(payload["detail"], "Invalid credentials.")
