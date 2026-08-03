import unittest
import json

from mangoo import Response, api_route, path
from mangoo.runtime import create_application


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
