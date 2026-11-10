import unittest

from hayavega.asgi import create_asgi_application
from hayavega.hardening import HardeningOptions
from hayavega.testing import WebSocketClosed, WebSocketTestSession, connect_websocket
from hayavega.websocket import WebSocket, WebSocketDisconnect, websocket_path


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

    async def test_oversized_message_is_rejected_when_hardening_configures_max_request_bytes(self):
        """``max_request_bytes`` already bounds HTTP request bodies when hardening is
        configured; a WebSocket message should be bounded by the same setting instead
        of being silently unlimited just because it arrives over a different transport."""
        options = HardeningOptions(max_request_bytes=16)
        app = create_asgi_application([], hardening=options, websocket_patterns=[websocket_path("ws/echo", _echo)])
        async with connect_websocket(app, "/ws/echo") as session:
            await session.send_text("this text is definitely longer than sixteen bytes")
            with self.assertRaises(WebSocketClosed) as ctx:
                await session.receive_text()
            self.assertEqual(ctx.exception.code, 1009)

    async def test_rejection_publishes_a_signal(self):
        from threading import Event

        from hayavega.addons.signals import SignalBus

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

        from hayavega.addons.signals import SignalBus

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


async def _sends_an_unexpected_message_first(scope, receive, send):
    await receive()  # websocket.connect
    await send({"type": "websocket.http.response.start"})  # neither accept nor close


class WebSocketTestSessionUnitTests(unittest.IsolatedAsyncioTestCase):
    """Drives ``WebSocketTestSession`` directly against a raw ASGI callable --
    the client-harness behavior these cover doesn't depend on Hayavega's own
    websocket dispatcher."""

    async def test_entering_the_session_rejects_an_unexpected_first_message_type(self):
        session = WebSocketTestSession(_sends_an_unexpected_message_first, "/ws/weird")
        with self.assertRaises(AssertionError):
            await session.__aenter__()
        # The handler task already ran to completion by the time the
        # unexpected message was read; await it so nothing is left pending.
        self.assertIsNotNone(session._task)
        await session._task


class WebSocketUnitTests(unittest.IsolatedAsyncioTestCase):
    """Drives ``WebSocket`` directly against fake ``receive``/``send``
    callables -- cheaper than going through a full ASGI round trip for
    behavior that doesn't depend on the dispatcher."""

    @staticmethod
    def _websocket(receive, send):
        return WebSocket({"path": "/ws/x", "query_string": b"", "headers": ()}, receive, send)

    async def test_accept_with_a_subprotocol_includes_it_in_the_accept_message(self):
        sent = []

        async def send(message):
            sent.append(message)

        ws = self._websocket(lambda: None, send)
        await ws.accept(subprotocol="graphql-ws")
        self.assertEqual(sent, [{"type": "websocket.accept", "subprotocol": "graphql-ws"}])

    async def test_receive_text_raises_disconnect_with_the_clients_code(self):
        async def receive():
            return {"type": "websocket.disconnect", "code": 4007}

        ws = self._websocket(receive, lambda message: None)
        with self.assertRaises(WebSocketDisconnect) as context:
            await ws.receive_text()
        self.assertEqual(context.exception.code, 4007)

    async def test_close_is_a_no_op_once_already_closed(self):
        sent = []

        async def send(message):
            sent.append(message)

        ws = self._websocket(lambda: None, send)
        await ws.close(code=1000)
        await ws.close(code=4001)  # second call must not send another close frame
        self.assertEqual(len(sent), 1)
        self.assertTrue(ws.closed)


if __name__ == "__main__":
    unittest.main()
