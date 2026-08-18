import tempfile
import unittest
from pathlib import Path
from threading import Event

from mangoo.addons.pulse import PulseBus
from mangoo.admin import admin_routes
from mangoo.auth import create_session, create_superuser
from mangoo.auth_api import auth_routes
from mangoo.db import connect
from mangoo.migrations import apply
from mangoo.response import Response
from mangoo.routing import path
from mangoo.runtime import create_application
from mangoo.testing import TestClient


PASSWORD = "Correct-Horse-7!"


class RuntimePulseTests(unittest.TestCase):
    def test_request_lifecycle_events_publish_asynchronously(self):
        bus, started, finished_event, finished = PulseBus(), Event(), Event(), {}

        def on_started(event):
            started.set()

        def on_finished(event):
            finished.update(event.data)
            finished_event.set()

        bus.connect("request.started", on_started)
        bus.connect("request.finished", on_finished)
        application = create_application([path("hello", lambda request: Response("hi"))], pulse=bus)
        client = TestClient(application)

        response = client.get("/hello")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(started.wait(timeout=2), "request.started was not published")
        self.assertTrue(finished_event.wait(timeout=2), "request.finished was not published")
        self.assertEqual(finished.get("status"), 200)
        self.assertEqual(finished.get("path"), "/hello")


class AuthApiPulseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_login_and_logout_publish_synchronous_events(self):
        bus, events = PulseBus(), []
        for name in ("auth.login_succeeded", "auth.login_failed", "auth.session_created", "auth.session_revoked"):
            bus.connect(name, lambda event, name=name: events.append(name))
        client = TestClient(create_application(auth_routes(self.database, pulse=bus)))

        client.post("/api/auth/login", json_data={"username": "admin", "password": "wrong-password"})
        self.assertEqual(events, ["auth.login_failed"])

        response = client.post("/api/auth/login", json_data={"username": "admin", "password": PASSWORD})
        token = response.json()["token"]
        self.assertEqual(events[1:], ["auth.login_succeeded", "auth.session_created"])

        # Logout with the bearer token only; drop the session cookie the login
        # response set so the CSRF-protection check (which only triggers for
        # cookie-authenticated requests) does not apply to this Bearer request.
        client.cookies.clear()
        logout_response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(logout_response.status_code, 204)
        self.assertEqual(events[3:], ["auth.session_revoked"])


class AdminPulseTests(unittest.TestCase):
    def test_admin_views_publish_admin_action(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "users.sqlite3"
            user = create_superuser(database, "admin", "admin@example.com", PASSWORD)
            token = create_session(database, user)
            bus, actions = PulseBus(), []
            bus.connect("admin.action", lambda event: actions.append(event.data["action"]))
            client = TestClient(create_application(admin_routes(database, pulse=bus)))

            client.get("/admin", headers={"Authorization": f"Bearer {token}"})
            client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})

            self.assertEqual(actions, ["index_viewed", "users_viewed"])


class MigrationPulseTests(unittest.TestCase):
    def test_migration_applied_publishes_after_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root, database = Path(temp) / "migrations", Path(temp) / "data.sqlite3"
            root.mkdir()
            (root / "0001_products.py").write_text(
                "operations = ['CREATE TABLE products (id INTEGER PRIMARY KEY)']", encoding="utf-8"
            )
            bus, seen_history_rows = PulseBus(), []

            def on_applied(event):
                connection = connect(database)
                try:
                    row = connection.execute("SELECT COUNT(*) AS total FROM mangoo_migrations WHERE name = ?", (event.data["name"],)).fetchone()
                    seen_history_rows.append(row["total"])
                finally:
                    connection.close()

            bus.connect("migration.applied", on_applied)
            applied = apply(database, [root], pulse=bus)

            self.assertEqual(applied, ["0001_products"])
            self.assertEqual(seen_history_rows, [1])


if __name__ == "__main__":
    unittest.main()
