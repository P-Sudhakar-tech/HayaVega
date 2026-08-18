import unittest
from threading import Event

from mangoo.addons.pulse import BUILTIN_EVENTS, PulseBus, PulseEvent


def _wait(event: Event, message: str) -> None:
    if not event.wait(timeout=2):
        raise AssertionError(message)


class PulseAddonTests(unittest.TestCase):
    def test_addon_exposes_immutable_event_contract_and_reserved_names(self):
        event = PulseEvent("auth.login_succeeded", actor_id=7, data={"method": "password"})
        self.assertIn(event.name, BUILTIN_EVENTS)
        with self.assertRaises(Exception):
            event.name = "changed"
        with self.assertRaises(TypeError):
            event.data["method"] = "token"

    def test_event_rejects_sensitive_payload_keys(self):
        with self.assertRaises(ValueError):
            PulseEvent("auth.login_succeeded", data={"password": "hunter2"})
        with self.assertRaises(ValueError):
            PulseEvent("auth.session_created", data={"Token": "abc"})

    def test_bus_delivers_in_connection_order_without_blocking_the_caller(self):
        bus, delivered, received = PulseBus(), Event(), []

        def first(event):
            received.append(("first", event.actor_id))

        def second(event):
            received.append(("second", event.actor_id))
            delivered.set()

        bus.connect("auth.login_succeeded", first)
        bus.subscribe("auth.login_succeeded", second)
        self.assertIsNone(bus.publish("auth.login_succeeded", actor_id=7))
        _wait(delivered, "subscribers were not invoked")
        self.assertEqual(received, [("first", 7), ("second", 7)])
        self.assertTrue(bus.disconnect("auth.login_succeeded", first))
        self.assertFalse(bus.unsubscribe("auth.login_succeeded", first))

    def test_bus_isolates_subscriber_errors(self):
        bus, delivered, received = PulseBus(), Event(), []

        def broken(event):
            raise RuntimeError("unavailable")

        def ok(event):
            received.append(event.name)
            delivered.set()

        bus.connect("request.finished", broken)
        bus.connect("request.finished", ok)
        bus.publish("request.finished")
        _wait(delivered, "the subscriber after the failing one was not invoked")
        self.assertEqual(received, ["request.finished"])

    def test_bus_rejects_invalid_event_names(self):
        with self.assertRaises(ValueError):
            PulseBus().connect("not valid", lambda event: None)

    def test_bus_delivers_to_many_subscribers_concurrently(self):
        bus, workers, delivered = PulseBus(workers=3), 3, Event()
        seen = []

        def make_subscriber(index):
            def subscriber(event):
                seen.append(index)
                if len(seen) == workers:
                    delivered.set()
            return subscriber

        for index in range(workers):
            bus.connect("migration.applied", make_subscriber(index))
        bus.publish("migration.applied", data={"name": "0001_initial"})
        _wait(delivered, "not all subscribers were invoked")
        self.assertEqual(sorted(seen), list(range(workers)))
