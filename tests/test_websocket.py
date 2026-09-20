import unittest

from mangoo.asgi import create_asgi_application
from mangoo.hardening import HardeningOptions
from mangoo.testing import WebSocketClosed, connect_websocket
from mangoo.websocket import WebSocketDisconnect, websocket_path


async def _echo(ws):
    await ws.accept()
    try:
        while True:
            await ws.send_text(await ws.receive_text())
    except WebSocketDisconnect:
        pass


async def _echo_json(ws):
    await ws.accept()
    try:
        while True:
            await ws.send_json(await ws.receive_json())
    except WebSocketDisconnect:
        pass


async def _reject_without_accepting(ws):
    return  # never calls accept() -- connection closes with the default code


async def _closes_with_custom_code(ws):
    await ws.accept()
    await ws.close(code=4001)


async def _raises_after_accepting(ws):
    await ws.accept()
    raise RuntimeError("boom")


class WebSocketDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_echo_round_trip(self):
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo") as session:
            self.assertTrue(session.accepted)
            await session.send_text("hello")
            self.assertEqual(await session.receive_text(), "hello")

    async def test_json_round_trip(self):
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/echo-json", _echo_json)])
        async with connect_websocket(app, "/ws/echo-json") as session:
            await session.send_json({"a": 1})
            self.assertEqual(await session.receive_json(), {"a": 1})

    async def test_unregistered_path_is_closed_immediately(self):
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/unknown") as session:
            self.assertFalse(session.accepted)
            self.assertEqual(session.close_code, 1000)

    async def test_handler_can_reject_without_accepting(self):
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/reject", _reject_without_accepting)])
        async with connect_websocket(app, "/ws/reject") as session:
            self.assertFalse(session.accepted)
            self.assertEqual(session.close_code, 1000)

    async def test_handler_close_code_reaches_the_client(self):
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/custom-close", _closes_with_custom_code)])
        async with connect_websocket(app, "/ws/custom-close") as session:
            with self.assertRaises(WebSocketClosed) as ctx:
                await session.receive_text()
            self.assertEqual(ctx.exception.code, 4001)

    async def test_handler_exception_after_accept_closes_with_1011(self):
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/boom", _raises_after_accepting)])
        async with connect_websocket(app, "/ws/boom") as session:
            with self.assertRaises(WebSocketClosed) as ctx:
                await session.receive_text()
            self.assertEqual(ctx.exception.code, 1011)

    async def test_client_disconnect_stops_the_echo_loop_cleanly(self):
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo") as session:
            await session.send_text("one")
            self.assertEqual(await session.receive_text(), "one")
        # __aexit__ sent websocket.disconnect; the handler's WebSocketDisconnect
        # catch means the background task completed without raising.


class WebSocketHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_disallowed_host_is_rejected_before_accept(self):
        options = HardeningOptions(allowed_hosts=("expected.example",))
        app = create_asgi_application([], hardening=options, websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo", headers={"Host": "attacker.example"}) as session:
            self.assertFalse(session.accepted)
            self.assertEqual(session.close_code, 4403)

    async def test_allowed_host_is_accepted(self):
        options = HardeningOptions(allowed_hosts=("expected.example",))
        app = create_asgi_application([], hardening=options, websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo", headers={"Host": "expected.example"}) as session:
            self.assertTrue(session.accepted)

    async def test_disallowed_origin_is_rejected(self):
        options = HardeningOptions(allowed_origins=("https://trusted.example",))
        app = create_asgi_application([], hardening=options, websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo", headers={"Origin": "https://evil.example"}) as session:
            self.assertFalse(session.accepted)
            self.assertEqual(session.close_code, 4403)

    async def test_missing_origin_is_not_rejected_when_allowed_origins_is_configured(self):
        """Only a *mismatched* Origin is rejected -- a non-browser client that
        sends no Origin header at all (common for server-to-server or CLI
        WebSocket clients) is not penalized for omitting a browser-specific header."""
        options = HardeningOptions(allowed_origins=("https://trusted.example",))
        app = create_asgi_application([], hardening=options, websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo") as session:
            self.assertTrue(session.accepted)

    async def test_wildcard_allowed_hosts_accepts_any_host(self):
        options = HardeningOptions(allowed_hosts=("*",))
        app = create_asgi_application([], hardening=options, websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo", headers={"Host": "anything.example"}) as session:
            self.assertTrue(session.accepted)

    async def test_rejection_publishes_a_signal(self):
        from threading import Event

        from mangoo.addons.signals import SignalBus

        bus = SignalBus()
        seen = Event()
        events = []

        def on_rejected(event):
            events.append(event.data)
            seen.set()

        bus.connect("websocket.rejected", on_rejected)
        options = HardeningOptions(allowed_hosts=("expected.example",))
        app = create_asgi_application([], hardening=options, signals=bus, websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo", headers={"Host": "attacker.example"}):
            pass
        seen.wait(2)
        self.assertEqual(events[0]["path"], "/ws/echo")


class WebSocketSignalsTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_and_disconnect_publish_signals(self):
        from threading import Event

        from mangoo.addons.signals import SignalBus

        bus = SignalBus()
        seen = Event()
        events = []

        def on_event(event):
            events.append(event.name)
            if len(events) >= 2:
                seen.set()

        bus.connect("websocket.connected", on_event)
        bus.connect("websocket.disconnected", on_event)
        app = create_asgi_application([], signals=bus, websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo") as session:
            await session.send_text("x")
            await session.receive_text()
        seen.wait(2)
        self.assertEqual(events, ["websocket.connected", "websocket.disconnected"])


if __name__ == "__main__":
    unittest.main()
