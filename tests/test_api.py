import io
import json
import unittest

from mangoo import api_route, path
from mangoo.graphql import Schema, graphql_view
from mangoo.runtime import create_application


def call(application, path_info, method="GET", body=b""):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "PATH_INFO": path_info,
        "REQUEST_METHOD": method,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    payload = b"".join(application(environ, start_response))
    return captured, payload


class RestApiTests(unittest.TestCase):
    def test_api_route_serializes_dictionary_as_json(self):
        app = create_application([api_route("api/health", lambda request: {"status": "ok"})])
        captured, body = call(app, "/api/health")

        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(captured["headers"]["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_route_rejects_unsupported_http_method(self):
        app = create_application([api_route("api/health", lambda request: {"status": "ok"})])
        captured, _ = call(app, "/api/health", method="POST")

        self.assertEqual(captured["status"], "405 Method Not Allowed")
        self.assertEqual(captured["headers"]["Allow"], "GET")


class GraphQLTests(unittest.TestCase):
    def setUp(self):
        self.schema = Schema(query={"greeting": lambda request: "Hello, GraphQL!"})
        self.app = create_application([path("graphql", graphql_view(self.schema), methods=("POST",))])

    def test_graphql_endpoint_executes_query(self):
        captured, body = call(self.app, "/graphql", method="POST", body=b'{"query":"{ greeting }"}')

        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(json.loads(body), {"data": {"greeting": "Hello, GraphQL!"}})

    def test_graphql_endpoint_reports_unknown_field(self):
        _, body = call(self.app, "/graphql", method="POST", body=b'{"query":"{ missing }"}')

        self.assertIn("Cannot query field", json.loads(body)["errors"][0]["message"])

    def test_graphql_endpoint_requires_query_json_field(self):
        captured, body = call(self.app, "/graphql", method="POST", body=b"{}")

        self.assertEqual(captured["status"], "400 Bad Request")
        self.assertIn("query", json.loads(body)["errors"][0]["message"])
