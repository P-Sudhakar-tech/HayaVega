import asyncio
import json
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from mangoo import HardeningOptions, api_route, path
from mangoo.addons.signals import SignalBus
from mangoo.asgi import _declared_content_length, _handle_websocket, _read_body, create_asgi_application
from mangoo.auth import create_session, create_superuser
from mangoo.metrics import MetricsRegistry
from mangoo.response import Response
from mangoo.security import csrf_protect, login_required
from mangoo.testing import AsyncTestClient
from mangoo.websocket import websocket_path


async def _raises_disconnect_without_catching_it(ws):
    """Unlike this project's other websocket test fixtures, this handler
    does NOT catch WebSocketDisconnect itself -- it propagates up to
    _handle_websocket's own try/except, exercising that `pass` branch."""
    await ws.accept()
    await ws.receive_text()


async def _raises_after_accepting_for_asgi_tests(ws):
    await ws.accept()
    raise RuntimeError("boom")


class AsgiApplicationTests(unittest.TestCase):
    def test_sync_view_runs_under_asgi(self):
        app = create_asgi_application([path("", lambda request: Response("Welcome"))])
        response = AsyncTestClient(app).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "Welcome")
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")

    def test_async_view_is_awaited_directly(self):
        async def view(request):
            return Response("async ok")

        app = create_asgi_application([path("", view)])
        response = AsyncTestClient(app).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "async ok")

    def test_plain_string_result_is_wrapped(self):
        app = create_asgi_application([path("health", lambda request: "healthy")])
        response = AsyncTestClient(app).get("/health/")
        self.assertEqual(response.text, "healthy")

    def test_async_view_can_return_a_plain_value(self):
        async def view(request):
            return {"status": "ok"}

        app = create_asgi_application([api_route("api/health", view)])
        response = AsyncTestClient(app).get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ok", response.text)

    def test_request_id_is_added_and_validated(self):
        app = create_asgi_application([path("", lambda request: Response("ok"))])
        response = AsyncTestClient(app).get("/")
        self.assertRegex(response.headers["x-request-id"], r"^[a-f0-9]{32}$")

    def test_unknown_route_returns_404(self):
        app = create_asgi_application([])
        response = AsyncTestClient(app).get("/missing/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"<h1>404 Not Found</h1>")

    def test_debug_404_names_the_requested_path_and_lists_registered_routes(self):
        app = create_asgi_application([path("health", lambda request: "ok")], debug=True)
        response = AsyncTestClient(app).get("/missing/")
        self.assertIn("/missing", response.text)
        self.assertIn("/health", response.text)

    def test_production_404_never_lists_registered_routes(self):
        app = create_asgi_application([path("health", lambda request: "ok")], debug=False)
        response = AsyncTestClient(app).get("/missing/")
        self.assertEqual(response.content, b"<h1>404 Not Found</h1>")
        self.assertNotIn(b"health", response.content)

    def test_wrong_method_returns_405_with_allow_header(self):
        app = create_asgi_application([path("", lambda request: Response("ok"))])
        response = AsyncTestClient(app).post("/")
        self.assertEqual(response.status_code, 405)
        self.assertIn("GET", response.headers["allow"])

    def test_openapi_document_is_served(self):
        app = create_asgi_application([api_route("api/health", lambda request: {"status": "ok"})])
        response = AsyncTestClient(app).get("/openapi.json")
        document = json.loads(response.text)
        self.assertEqual(document["openapi"], "3.0.3")
        self.assertIn("/api/health", document["paths"])

    def test_request_body_is_readable_as_json(self):
        captured = {}

        async def view(request):
            captured["body"] = request.json()
            return Response("ok")

        app = create_asgi_application([path("echo", view, methods=("POST",))])
        AsyncTestClient(app).post("/echo", json_data={"name": "mangoo"})
        self.assertEqual(captured["body"], {"name": "mangoo"})

    def test_hardening_rejects_disallowed_host(self):
        app = create_asgi_application([path("", lambda request: Response("ok"))], hardening=HardeningOptions(allowed_hosts=("example.com",)))
        response = AsyncTestClient(app).get("/", headers={"Host": "evil.com"})
        self.assertEqual(response.status_code, 400)

    def test_hardening_oversized_body_is_rejected_without_reading_it(self):
        app = create_asgi_application([path("echo", lambda request: Response("ok"), methods=("POST",))], hardening=HardeningOptions(max_request_bytes=4))
        response = AsyncTestClient(app).post("/echo", json_data={"name": "too-long-for-the-limit"})
        self.assertEqual(response.status_code, 413)

    def test_unhandled_view_exception_becomes_500(self):
        async def view(request):
            raise RuntimeError("boom")

        app = create_asgi_application([path("", view)])
        with self.assertLogs("mangoo.asgi", level="ERROR"):
            response = AsyncTestClient(app).get("/")
        self.assertEqual(response.status_code, 500)

    def test_unhandled_exception_shows_error_type_but_hides_message_and_traceback(self):
        async def view(request):
            raise ValueError("db password is hunter2 at host 10.0.0.5")

        app = create_asgi_application([path("", view)])
        with self.assertLogs("mangoo.asgi", level="ERROR"):
            response = AsyncTestClient(app).get("/")
        self.assertEqual(response.status_code, 500)
        self.assertIn("ValueError", response.text)
        self.assertNotIn("hunter2", response.text)
        self.assertNotIn("10.0.0.5", response.text)
        self.assertNotIn("Traceback", response.text)

    def test_api_route_error_returns_a_json_envelope_not_html(self):
        from mangoo import api_route

        async def broken(request):
            raise ValueError("db password is hunter2 at host 10.0.0.5")

        app = create_asgi_application([api_route("broken", broken)])
        with self.assertLogs("mangoo.asgi", level="ERROR"):
            response = AsyncTestClient(app).get("/broken")
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body["error_type"], "ValueError")
        self.assertNotIn("hunter2", body["detail"])

    def test_signals_publish_request_started_and_finished(self):
        bus = SignalBus()
        received = []
        bus.connect("request.started", lambda event: received.append(event.name))
        bus.connect("request.finished", lambda event: received.append(event.name))
        app = create_asgi_application([path("", lambda request: Response("ok"))], signals=bus)
        AsyncTestClient(app).get("/")
        bus.close()
        # Signals deliver asynchronously on a worker pool; give it a beat to land.
        asyncio.run(asyncio.sleep(0.05))
        self.assertIn("request.started", received)
        self.assertIn("request.finished", received)

    def test_deprecated_pulse_alias_still_works_and_warns(self):
        bus = SignalBus()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            app = create_asgi_application([path("", lambda request: Response("ok"))], pulse=bus)
        self.assertTrue(any(issubclass(item.category, DeprecationWarning) for item in caught))
        response = AsyncTestClient(app).get("/")
        self.assertEqual(response.status_code, 200)
        bus.close()

    def test_metrics_are_recorded(self):
        registry = MetricsRegistry()
        app = create_asgi_application([path("", lambda request: Response("ok"))], metrics=registry)
        AsyncTestClient(app).get("/")
        snapshot = registry.snapshot()
        self.assertEqual(snapshot["counts"][("GET", "", 200)], 1)

    def test_lifespan_scope_is_acknowledged(self):
        app = create_asgi_application([])
        events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        sent = []

        async def receive():
            return next(events)

        async def send(message):
            sent.append(message)

        asyncio.run(app({"type": "lifespan"}, receive, send))
        self.assertEqual([message["type"] for message in sent], ["lifespan.startup.complete", "lifespan.shutdown.complete"])

    def test_login_required_composes_with_an_async_view(self):
        """A decorator wrapping an async view must itself stay a coroutine
        function, or the ASGI runtime would run it in a thread and get back
        an unawaited coroutine instead of a Response -- the exact bug this
        test guards against (found while adding ASGI support)."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "users.sqlite3"
            user = create_superuser(database, "admin", "admin@example.com", "Correct-Horse-7!")
            token = create_session(database, user)

            async def view(request):
                return Response("secret")

            protected = login_required(database)(view)
            app = create_asgi_application([path("secret", protected)])
            unauthorized = AsyncTestClient(app).get("/secret")
            self.assertEqual(unauthorized.status_code, 401)
            authorized = AsyncTestClient(app).get("/secret", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(authorized.status_code, 200)
            self.assertEqual(authorized.text, "secret")

    def test_csrf_protect_composes_with_an_async_view(self):
        async def view(request):
            return Response("posted")

        protected = csrf_protect(view)
        app = create_asgi_application([path("form", protected, methods=("POST",))])
        # A session cookie without a matching CSRF token must be rejected;
        # this is what actually exercises the decorator's async path.
        response = AsyncTestClient(app).post("/form", headers={"Cookie": "mangoo_session=some-session-token"})
        self.assertEqual(response.status_code, 403)

    def test_form_data_is_url_encoded_and_sent_with_the_form_content_type(self):
        captured = {}

        async def view(request):
            captured["content_type"] = request.headers.get("Content-Type")
            captured["form"] = request.form()
            return Response("ok")

        app = create_asgi_application([path("echo", view, methods=("POST",))])
        AsyncTestClient(app).post("/echo", form_data={"name": "mangoo", "lang": "python"})
        self.assertEqual(captured["content_type"], "application/x-www-form-urlencoded")
        self.assertEqual(captured["form"], {"name": "mangoo", "lang": "python"})

    def test_receive_returns_http_disconnect_once_the_body_has_already_been_delivered(self):
        """Exercises AsyncTestClient's own `receive()` closure directly with a
        raw ASGI callable, rather than through Mangoo's routing -- which
        never calls `receive()` a second time for an ordinary request."""

        async def app(scope, receive, send):
            await receive()
            second = await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": second["type"].encode("utf-8")})

        response = AsyncTestClient(app).get("/")
        self.assertEqual(response.text, "http.disconnect")

    def test_set_cookie_response_header_is_captured_in_the_client_cookie_jar(self):
        async def app(scope, receive, send):
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": [(b"set-cookie", b"session=abc123; Path=/")]})
            await send({"type": "http.response.body", "body": b"ok"})

        client = AsyncTestClient(app)
        client.get("/")
        self.assertEqual(client.cookies, {"session": "abc123"})

    def test_unsupported_scope_type_raises(self):
        app = create_asgi_application([])

        async def receive():
            return {}

        async def send(message):
            pass

        with self.assertRaises(ValueError):
            asyncio.run(app({"type": "grpc"}, receive, send))

    def test_an_uncaught_websocket_disconnect_from_the_handler_is_swallowed(self):
        """A handler that doesn't catch WebSocketDisconnect itself must not
        propagate it out of _handle_websocket -- the connection is already
        gone, there is nothing left to respond to."""
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/boom", _raises_disconnect_without_catching_it)])
        messages = iter([{"type": "websocket.connect"}, {"type": "websocket.disconnect", "code": 1000}])

        async def receive():
            return next(messages)

        sent = []

        async def send(message):
            sent.append(message)

        asyncio.run(app({"type": "websocket", "path": "/ws/boom", "headers": []}, receive, send))  # must not raise
        # The disconnect itself is swallowed; the always-run `finally` still closes out cleanly afterward.
        self.assertEqual(sent, [{"type": "websocket.accept"}, {"type": "websocket.close", "code": 1000}])

    def test_a_close_failure_after_a_handler_exception_is_swallowed(self):
        """If the handler raises AND the subsequent close() itself fails
        (the connection is already gone), both failures must be swallowed
        -- including the second close attempt in the always-run `finally`."""
        app = create_asgi_application([], websocket_patterns=[websocket_path("ws/boom", _raises_after_accepting_for_asgi_tests)])

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            if message["type"] == "websocket.close":
                raise RuntimeError("connection already gone")

        asyncio.run(app({"type": "websocket", "path": "/ws/boom", "headers": []}, receive, send))  # must not raise

    def test_pool_connections_enables_connection_pooling(self):
        with patch("mangoo.asgi.db.enable_connection_pooling") as enable:
            create_asgi_application([], pool_connections=True)
        enable.assert_called_once_with()

    def test_pool_connections_defaults_to_disabled(self):
        with patch("mangoo.asgi.db.enable_connection_pooling") as enable:
            create_asgi_application([])
        enable.assert_not_called()

    def test_preflight_options_request_with_an_allowed_origin_is_answered_before_dispatch(self):
        app = create_asgi_application([path("", lambda request: Response("ok"))], hardening=HardeningOptions(allowed_origins=("https://example.test",)))
        response = AsyncTestClient(app).request("OPTIONS", "/", headers={"Origin": "https://example.test"})
        self.assertEqual(response.status_code, 204)


class AsgiHelperFunctionTests(unittest.TestCase):
    """Direct unit tests of small ``mangoo.asgi`` helpers whose branches
    aren't reachable through ``AsyncTestClient`` (which always delivers a
    single, well-formed ``http.request`` message and never a malformed
    ``content-length``)."""

    def test_read_body_skips_a_message_that_is_not_http_request(self):
        messages = iter([{"type": "http.disconnect"}, {"type": "http.request", "body": b"hello", "more_body": False}])

        async def receive():
            return next(messages)

        body = asyncio.run(_read_body(receive))
        self.assertEqual(body, b"hello")

    def test_declared_content_length_returns_zero_for_a_malformed_header(self):
        scope = {"headers": [(b"content-length", b"not-a-number")]}
        self.assertEqual(_declared_content_length(scope), 0)

    def test_declared_content_length_returns_zero_when_absent(self):
        self.assertEqual(_declared_content_length({"headers": []}), 0)

    def test_handle_websocket_returns_before_accepting_on_a_non_connect_message(self):
        sent = []

        async def receive():
            return {"type": "websocket.disconnect", "code": 1000}

        async def send(message):
            sent.append(message)

        asyncio.run(_handle_websocket({"path": "/ws"}, receive, send, route=None, hardening_layer=None, signals=None))
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
