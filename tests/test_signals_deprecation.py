"""Proof that the Pulse -> Signals rename kept every old call site working.

``hayavega.addons.pulse`` and every ``pulse=`` keyword argument across the
framework are deprecated aliases for ``hayavega.addons.signals`` and
``signals=``: each should still work exactly as before, while raising a
single, clear ``DeprecationWarning`` pointing at the replacement.
"""

import importlib
import tempfile
import unittest
import warnings
from pathlib import Path
from threading import Event

from hayavega.addons.signals import SignalBus
from hayavega.admin import admin_routes
from hayavega.auth import create_superuser
from hayavega.auth_api import auth_routes
from hayavega.migrations import apply
from hayavega.response import Response
from hayavega.routing import path
from hayavega.runtime import create_application
from hayavega.testing import TestClient

PASSWORD = "Correct-Horse-7!"


def _wait(event: Event, message: str) -> None:
    if not event.wait(timeout=2):
        raise AssertionError(message)


class PulsePackageAliasTests(unittest.TestCase):
    def test_importing_hayavega_addons_pulse_warns(self):
        import hayavega.addons.pulse as pulse_module

        with self.assertWarns(DeprecationWarning):
            importlib.reload(pulse_module)

    def test_pulse_names_are_the_same_objects_as_their_signals_replacements(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from hayavega.addons.pulse import BUILTIN_EVENTS as PULSE_EVENTS
            from hayavega.addons.pulse import PulseBus, PulseEvent, PulseSubscriber

        from hayavega.addons.signals import BUILTIN_EVENTS, SignalBus, SignalEvent, SignalSubscriber

        self.assertIs(PulseBus, SignalBus)
        self.assertIs(PulseEvent, SignalEvent)
        self.assertIs(PulseSubscriber, SignalSubscriber)
        self.assertEqual(PULSE_EVENTS, BUILTIN_EVENTS)


class DeprecatedPulseKeywordTests(unittest.TestCase):
    """Each call site's `pulse=` kwarg: still functions, and still warns."""

    def test_create_application_pulse_kwarg_warns_and_still_delivers_events(self):
        bus, finished = SignalBus(), Event()
        bus.connect("request.finished", lambda event: finished.set())

        with self.assertWarns(DeprecationWarning):
            app = create_application([path("hello", lambda request: Response("hi"))], pulse=bus)

        TestClient(app).get("/hello")
        _wait(finished, "request.finished was not published via the deprecated pulse= kwarg")

    def test_create_application_pulse_and_signals_together_prefers_signals(self):
        used, unused = SignalBus(), SignalBus()
        seen = Event()
        used.connect("request.finished", lambda event: seen.set())
        unused_seen = Event()
        unused.connect("request.finished", lambda event: unused_seen.set())

        with self.assertWarns(DeprecationWarning):
            app = create_application([path("hello", lambda request: Response("hi"))], signals=used, pulse=unused)

        TestClient(app).get("/hello")
        _wait(seen, "the explicit signals= bus should have received the event")
        self.assertFalse(unused_seen.is_set(), "the deprecated pulse= bus should be ignored when signals= is also given")

    def test_auth_routes_pulse_kwarg_warns_and_still_delivers_events(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "users.sqlite3"
            create_superuser(database, "admin", "admin@example.com", PASSWORD)
            bus, failed = SignalBus(), Event()
            bus.connect("auth.login_failed", lambda event: failed.set())

            with self.assertWarns(DeprecationWarning):
                app = create_application(auth_routes(database, pulse=bus))

            TestClient(app).post("/api/auth/login", json_data={"username": "admin", "password": "wrong"})
            _wait(failed, "auth.login_failed was not published via the deprecated pulse= kwarg")

    def test_admin_routes_pulse_kwarg_warns_and_still_delivers_events(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "users.sqlite3"
            create_superuser(database, "admin", "admin@example.com", PASSWORD)
            bus, viewed = SignalBus(), Event()
            bus.connect("admin.action", lambda event: viewed.set())

            with self.assertWarns(DeprecationWarning):
                app = create_application(admin_routes(database, pulse=bus))

            from hayavega.auth import create_session, get_user_by_username

            user = get_user_by_username(database, "admin")
            token = create_session(database, user)
            TestClient(app).get("/admin", headers={"Authorization": f"Bearer {token}"})
            _wait(viewed, "admin.action was not published via the deprecated pulse= kwarg")

    def test_migrations_apply_pulse_kwarg_warns_and_still_delivers_events(self):
        with tempfile.TemporaryDirectory() as temp:
            root, database = Path(temp) / "migrations", Path(temp) / "data.sqlite3"
            root.mkdir()
            (root / "0001_products.py").write_text("operations = ['CREATE TABLE products (id INTEGER PRIMARY KEY)']", encoding="utf-8")
            bus, applied_event = SignalBus(), Event()
            bus.connect("migration.applied", lambda event: applied_event.set())

            with self.assertWarns(DeprecationWarning):
                applied = apply(database, [root], pulse=bus)

            self.assertEqual(applied, ["0001_products"])
            _wait(applied_event, "migration.applied was not published via the deprecated pulse= kwarg")


if __name__ == "__main__":
    unittest.main()
