import importlib.util
import sys
import unittest
from threading import Event

from hayavega.addons.signals import BUILTIN_EVENTS, SignalBus, SignalEvent
from hayavega.addons.signals import registry as registry_module
from hayavega.addons.signals.registry import _FallbackDispatcher


def _wait(event: Event, message: str) -> None:
    if not event.wait(timeout=2):
        raise AssertionError(message)


class SignalsAddonTests(unittest.TestCase):
    def test_addon_exposes_immutable_event_contract_and_reserved_names(self):
        event = SignalEvent("auth.login_succeeded", actor_id=7, data={"method": "password"})
        self.assertIn(event.name, BUILTIN_EVENTS)
        with self.assertRaises(Exception):  # noqa: B017 -- frozen-dataclass mutation; exact type is a dataclasses implementation detail.
            event.name = "changed"
        with self.assertRaises(TypeError):
            event.data["method"] = "token"

    def test_event_rejects_sensitive_payload_keys(self):
        with self.assertRaises(ValueError):
            SignalEvent("auth.login_succeeded", data={"password": "hunter2"})
        with self.assertRaises(ValueError):
            SignalEvent("auth.session_created", data={"Token": "abc"})

    def test_bus_close_stops_its_worker_pool(self):
        bus = SignalBus()
        bus.close()  # must return promptly; no assertion needed beyond "doesn't hang"

    def test_event_rejects_an_empty_or_non_string_name(self):
        with self.assertRaises(ValueError):
            SignalEvent("")
        with self.assertRaises(ValueError):
            SignalEvent(123)

    def test_bus_delivers_in_connection_order_without_blocking_the_caller(self):
        bus, delivered, received = SignalBus(), Event(), []

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
        bus, delivered, received = SignalBus(), Event(), []

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
            SignalBus().connect("not valid", lambda event: None)

    def test_connect_rejects_a_non_callable_subscriber(self):
        with self.assertRaises(TypeError):
            SignalBus().connect("request.started", "not callable")

    def test_disconnect_removes_the_event_entry_once_its_last_subscriber_is_gone(self):
        bus = SignalBus()
        subscriber = lambda event: None  # noqa: E731
        bus.connect("request.started", subscriber)
        self.assertTrue(bus.disconnect("request.started", subscriber))
        self.assertNotIn("request.started", bus._subscribers)

    def test_emit_rejects_anything_that_is_not_a_signal_event(self):
        with self.assertRaises(TypeError):
            SignalBus().emit("not-an-event")

    def test_bus_delivers_to_many_subscribers_concurrently(self):
        bus, workers, delivered = SignalBus(workers=3), 3, Event()
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


class FallbackDispatcherTests(unittest.TestCase):
    """Exercises the pure-Python ``ThreadPoolExecutor`` dispatcher directly --
    it's only reached through ``SignalBus`` when ``hayavega_native`` isn't
    built, which isn't the case in this environment."""

    def test_dispatch_submits_delivery_to_the_worker_pool(self):
        dispatcher = _FallbackDispatcher(workers=2)
        try:
            delivered, received = Event(), []

            def subscriber(event):
                received.append(event.name)
                delivered.set()

            dispatcher.dispatch([subscriber], SignalEvent("request.started"))
            _wait(delivered, "the fallback dispatcher did not deliver the event")
            self.assertEqual(received, ["request.started"])
        finally:
            dispatcher.close()

    def test_a_failing_subscriber_is_logged_and_does_not_block_later_subscribers(self):
        dispatcher = _FallbackDispatcher(workers=1)
        try:
            delivered, received = Event(), []

            def broken(event):
                raise RuntimeError("unavailable")

            def ok(event):
                received.append(event.name)
                delivered.set()

            with self.assertLogs("hayavega.signals", level="ERROR"):
                dispatcher.dispatch([broken, ok], SignalEvent("request.finished"))
                _wait(delivered, "the subscriber after the failing one was not invoked")
            self.assertEqual(received, ["request.finished"])
        finally:
            dispatcher.close()

    def test_close_shuts_down_the_pool_without_blocking(self):
        dispatcher = _FallbackDispatcher(workers=1)
        dispatcher.close()  # must return promptly (wait=False); no assertion needed beyond "doesn't hang"


class NativeDispatcherImportFallbackTests(unittest.TestCase):
    """`registry.py`'s `try: import hayavega_native ... except ImportError:
    _NativeDispatcher = None` only runs at module import time. Loading a
    throwaway copy of the module from the same source file (rather than
    `importlib.reload`-ing the shared one in place) avoids desyncing class
    identity for every other test that already imported names from it --
    same technique as hayavega.db's own equivalent fallback test."""

    def test_module_level_import_falls_back_when_hayavega_native_is_unavailable(self):
        original = sys.modules.get("hayavega_native", None)
        sys.modules["hayavega_native"] = None  # sentinel: forces ImportError on import
        try:
            # The throwaway name must live under "hayavega.addons.signals" so
            # the module's own relative import ("from .contracts import
            # ...") still resolves against the real, already-imported
            # contracts module instead of a nonexistent sibling.
            spec = importlib.util.spec_from_file_location("hayavega.addons.signals._registry_reimport_test", registry_module.__file__)
            fresh_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fresh_module)
            self.assertIsNone(fresh_module._NativeDispatcher)
        finally:
            if original is not None:
                sys.modules["hayavega_native"] = original
            else:
                del sys.modules["hayavega_native"]
