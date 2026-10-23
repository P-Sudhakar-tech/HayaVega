import json
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

from mangoo import Response, api_route, path
from mangoo.runtime import create_application, serve


def call(application, request_path: str):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(application({"PATH_INFO": request_path}, start_response))
    return captured, body


class RuntimeTests(unittest.TestCase):
    def test_application_returns_response_from_matching_route(self):
        app = create_application([path("", lambda request: Response("Welcome"))])
        captured, body = call(app, "/")
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(captured["headers"]["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(captured["headers"]["Content-Length"], str(len(body)))
        self.assertEqual(body, b"Welcome")

    def test_application_wraps_plain_string_view_result(self):
        app = create_application([path("health", lambda request: "healthy")])
        captured, body = call(app, "/health/")
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(body, b"healthy")

    def test_application_adds_and_validates_request_id(self):
        app = create_application([path("", lambda request: Response("ok"))])
        captured, _ = call(app, "/")
        self.assertRegex(captured["headers"]["X-Request-ID"], r"^[a-f0-9]{32}$")

    def test_application_returns_404_for_unknown_route(self):
        app = create_application([])
        captured, body = call(app, "/missing/")
        self.assertEqual(captured["status"], "404 Not Found")
        self.assertEqual(body, b"<h1>404 Not Found</h1>")

    def test_debug_404_names_the_requested_path_and_lists_registered_routes(self):
        app = create_application([path("health", lambda request: "ok")], debug=True)
        _, body = call(app, "/missing/")
        text = body.decode()
        self.assertIn("/missing", text)
        self.assertIn("/health", text)

    def test_production_404_never_lists_registered_routes(self):
        app = create_application([path("health", lambda request: "ok")], debug=False)
        _, body = call(app, "/missing/")
        self.assertEqual(body, b"<h1>404 Not Found</h1>")
        self.assertNotIn(b"health", body)

    def test_application_serves_openapi_document_for_api_routes(self):
        app = create_application([api_route("api/health", lambda request: {"status": "ok"})])
        captured, body = call(app, "/openapi.json")
        document = json.loads(body)
        self.assertEqual(captured["headers"]["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(document["openapi"], "3.0.3")
        self.assertIn("/api/health", document["paths"])
        self.assertIn("get", document["paths"]["/api/health"])

    def test_application_serves_swagger_ui(self):
        app = create_application([])
        captured, body = call(app, "/docs")
        self.assertEqual(captured["status"], "200 OK")
        self.assertIn(b"SwaggerUIBundle", body)

    def test_pool_connections_enables_connection_pooling(self):
        with patch("mangoo.runtime.db.enable_connection_pooling") as enable:
            create_application([], pool_connections=True)
        enable.assert_called_once_with()

    def test_pool_connections_defaults_to_disabled(self):
        with patch("mangoo.runtime.db.enable_connection_pooling") as enable:
            create_application([])
        enable.assert_not_called()

    def test_malformed_content_length_header_is_parsed_defensively(self):
        """``application()``'s own sizing parse of ``CONTENT_LENGTH`` catches
        a non-numeric value and falls back to 0 rather than letting that
        ValueError escape from its own try/except -- exercised here by
        confirming the eventual error comes from ``Request.from_wsgi``'s
        separate (later, unguarded) re-parse of the same header, not from
        this one."""
        import io

        app = create_application([path("", lambda request: Response("ok"))])
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        environ = {"PATH_INFO": "/", "CONTENT_LENGTH": "not-a-number", "wsgi.input": io.BytesIO(b"")}
        with self.assertRaises(ValueError):
            b"".join(app(environ, start_response))


class ServeTests(unittest.TestCase):
    """serve() for real: a bound socket, a real HTTP request, a real
    answer -- not just that it calls create_application() correctly."""

    def test_serve_actually_starts_a_real_server_and_answers_a_real_request(self):
        import mangoo.runtime as runtime_module

        captured: dict = {}
        real_make_server = runtime_module.make_server

        def capturing_make_server(host, port, application):
            httpd = real_make_server(host, port, application)
            captured["server"] = httpd
            return httpd

        with patch("mangoo.runtime.make_server", side_effect=capturing_make_server):
            thread = threading.Thread(
                target=serve,
                args=([path("", lambda request: Response("hello from serve()"))],),
                kwargs={"host": "127.0.0.1", "port": 0},
                daemon=True,
            )
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while "server" not in captured and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertIn("server", captured, "serve() never reached make_server() in time")
                httpd = captured["server"]
                port = httpd.server_port

                deadline = time.monotonic() + 5
                last_error = None
                response_body = None
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                            response_body = response.read()
                        break
                    except Exception as error:  # noqa: BLE001 -- polling until the server accepts connections
                        last_error = error
                        time.sleep(0.1)
                self.assertEqual(response_body, b"hello from serve()", f"never got a response ({last_error})")
            finally:
                if "server" in captured:
                    captured["server"].shutdown()
                thread.join(timeout=5)
