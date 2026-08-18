import unittest
from threading import Event

from mangoo.addons.pulse import BUILTIN_EVENTS, PulseBus, PulseEvent


class PulseAddonTests(unittest.TestCase):
    def test_addon_exposes_immutable_event_contract_and_reserved_names(self):
        event = PulseEvent("auth.login_succeeded", actor_id=7, data={"method": "password"})
        self.assertIn(event.name, BUILTIN_EVENTS)
        with self.assertRaises(Exception):
            event.name = "changed"
        with self.assertRaises(TypeError):
            event.data["method"] = "token"

    def test_bus_delivers_in_connection_order_and_disconnects(self):
        bus, received = PulseBus(), []
        first = lambda event: received.append(("first", event.actor_id))
        second = lambda event: received.append(("second", event.actor_id))
        bus.connect("auth.login_succeeded", first)
        bus.subscribe("auth.login_succeeded", second)
        self.assertEqual(bus.publish("auth.login_succeeded", actor_id=7), ())
        self.assertEqual(received, [("first", 7), ("second", 7)])
        self.assertTrue(bus.disconnect("auth.login_succeeded", first))
        self.assertFalse(bus.unsubscribe("auth.login_succeeded", first))

    def test_bus_isolates_subscriber_errors(self):
        bus, received = PulseBus(), []
        def broken(event):
            raise RuntimeError("unavailable")
        bus.connect("request.finished", broken)
        bus.connect("request.finished", lambda event: received.append(event.name))
        failures = bus.publish("request.finished")
        self.assertEqual(received, ["request.finished"])
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0].error, RuntimeError)

    def test_bus_rejects_invalid_event_names(self):
        with self.assertRaises(ValueError):
            PulseBus().connect("not valid", lambda event: None)

    def test_event_rejects_sensitive_payload_keys(self):
        with self.assertRaises(ValueError):
            PulseEvent("auth.login_succeeded", data={"password": "hunter2"})
        with self.assertRaises(ValueError):
            PulseEvent("auth.session_created", data={"Token": "abc"})

    def test_bus_delivers_asynchronously_without_blocking_the_caller(self):
        bus, delivered = PulseBus(), Event()
        captured = {}

        def slow(event):
            captured["actor_id"] = event.actor_id
            delivered.set()

        bus.connect("request.finished", slow)
        bus.publish_async("request.finished", actor_id=3)
        self.assertTrue(delivered.wait(timeout=2), "async subscriber was not invoked")
        self.assertEqual(captured["actor_id"], 3)

    def test_async_delivery_isolates_subscriber_errors(self):
        bus, delivered = PulseBus(), Event()

        def broken(event):
            raise RuntimeError("unavailable")

        def ok(event):
            delivered.set()

        bus.connect("migration.applied", broken)
        bus.connect("migration.applied", ok)
        bus.publish_async("migration.applied", data={"name": "0001_initial"})
        self.assertTrue(delivered.wait(timeout=2), "later async subscriber was not invoked")
