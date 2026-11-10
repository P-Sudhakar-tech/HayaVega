import tempfile
import unittest
from pathlib import Path
from threading import Event

from hayavega.addons.signals import SignalBus
from hayavega.admin import admin_routes
from hayavega.auth import create_session, create_superuser
from hayavega.auth_api import auth_routes
from hayavega.db import connect
from hayavega.migrations import apply
from hayavega.response import Response
from hayavega.routing import path
from hayavega.runtime import create_application
from hayavega.testing import TestClient

PASSWORD = "Correct-Horse-7!"


def _wait(event: Event, message: str) -> None:
    if not event.wait(timeout=2):
        raise AssertionError(message)


class RuntimeSignalsTests(unittest.TestCase):
    def test_request_lifecycle_events_publish_without_blocking_the_response(self):
        bus, started, finished_event, finished = SignalBus(), Event(), Event(), {}

        def on_started(event):
            started.set()

        def on_finished(event):
            finished.update(event.data)
            finished_event.set()

        bus.connect("request.started", on_started)
        bus.connect("request.finished", on_finished)
        application = create_application([path("hello", lambda request: Response("hi"))], signals=bus)
        client = TestClient(application)

        response = client.get("/hello")

        self.assertEqual(response.status_code, 200)
        _wait(started, "request.started was not published")
        _wait(finished_event, "request.finished was not published")
        self.assertEqual(finished.get("status"), 200)
        self.assertEqual(finished.get("path"), "/hello")


class AuthApiSignalsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        create_superuser(self.database, "admin", "admin@example.com", PASSWORD)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_login_and_logout_publish_events(self):
        bus = SignalBus()
        signals = {name: Event() for name in ("auth.login_succeeded", "auth.login_failed", "auth.session_created", "auth.session_revoked")}
        for name, signal in signals.items():
            bus.connect(name, lambda event, signal=signal: signal.set())
        client = TestClient(create_application(auth_routes(self.database, signals=bus)))

        client.post("/api/auth/login", json_data={"username": "admin", "password": "wrong-password"})
        _wait(signals["auth.login_failed"], "auth.login_failed was not published")

        response = client.post("/api/auth/login", json_data={"username": "admin", "password": PASSWORD})
        token = response.json()["token"]
        _wait(signals["auth.login_succeeded"], "auth.login_succeeded was not published")
        _wait(signals["auth.session_created"], "auth.session_created was not published")

        # Logout with the bearer token only; drop the session cookie the login
        # response set so the CSRF-protection check (which only triggers for
        # cookie-authenticated requests) does not apply to this Bearer request.
        client.cookies.clear()
        logout_response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(logout_response.status_code, 204)
        _wait(signals["auth.session_revoked"], "auth.session_revoked was not published")


class AdminSignalsTests(unittest.TestCase):
    def test_admin_views_publish_admin_action(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "users.sqlite3"
            user = create_superuser(database, "admin", "admin@example.com", PASSWORD)
            token = create_session(database, user)
            bus, actions, both_seen = SignalBus(), [], Event()

            def on_action(event):
                actions.append(event.data["action"])
                if len(actions) == 2:
                    both_seen.set()

            bus.connect("admin.action", on_action)
            client = TestClient(create_application(admin_routes(database, signals=bus)))

            client.get("/admin", headers={"Authorization": f"Bearer {token}"})
            client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})

            _wait(both_seen, "admin.action was not published twice")
            self.assertEqual(sorted(actions), ["index_viewed", "users_viewed"])


class MigrationSignalsTests(unittest.TestCase):
    def test_migration_applied_publishes_after_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root, database = Path(temp) / "migrations", Path(temp) / "data.sqlite3"
            root.mkdir()
            (root / "0001_products.py").write_text("operations = ['CREATE TABLE products (id INTEGER PRIMARY KEY)']", encoding="utf-8")
            bus, seen_history_rows, delivered = SignalBus(), [], Event()

            def on_applied(event):
                connection = connect(database)
                try:
                    row = connection.execute("SELECT COUNT(*) AS total FROM hayavega_migrations WHERE name = ?", (event.data["name"],)).fetchone()
                    seen_history_rows.append(row["total"])
                finally:
                    connection.close()
                delivered.set()

            bus.connect("migration.applied", on_applied)
            applied = apply(database, [root], signals=bus)

            self.assertEqual(applied, ["0001_products"])
            _wait(delivered, "migration.applied was not published")
            self.assertEqual(seen_history_rows, [1])


if __name__ == "__main__":
    unittest.main()
