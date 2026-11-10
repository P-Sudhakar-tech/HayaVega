import unittest

from hayavega.auth_api import auth_routes
from hayavega.rest import api_route
from hayavega.runtime import create_application
from hayavega.swagger import ERROR_SCHEMA, openapi_document
from hayavega.testing import TestClient


class OpenApiDocumentTests(unittest.TestCase):
    def test_document_always_declares_the_bearer_scheme_and_error_schema(self):
        document = openapi_document([api_route("api/health", lambda request: {"status": "ok"})])
        self.assertEqual(document["components"]["securitySchemes"]["bearerAuth"], {"type": "http", "scheme": "bearer", "description": document["components"]["securitySchemes"]["bearerAuth"]["description"]})
        self.assertEqual(document["components"]["schemas"]["Error"], ERROR_SCHEMA)

    def test_routes_without_metadata_keep_the_generic_fallback(self):
        document = openapi_document([api_route("api/items", lambda request: [], methods=("GET", "POST"))])
        operation = document["paths"]["/api/items"]
        self.assertEqual(operation["get"]["responses"]["200"]["content"]["application/json"]["schema"], {"type": "object"})
        self.assertEqual(operation["post"]["requestBody"]["content"]["application/json"]["schema"], {"type": "object"})
        self.assertFalse(operation["post"]["requestBody"]["required"])
        self.assertNotIn("security", operation["get"])

    def test_secured_route_gets_a_security_requirement_and_a_401_response(self):
        document = openapi_document([api_route("api/private", lambda request: {}, secured=True)])
        operation = document["paths"]["/api/private"]["get"]
        self.assertEqual(operation["security"], [{"bearerAuth": []}])
        self.assertEqual(operation["responses"]["401"]["content"]["application/json"]["schema"], ERROR_SCHEMA)

    def test_declared_request_and_response_schemas_are_used_verbatim(self):
        request_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        response_schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        document = openapi_document(
            [
                api_route(
                    "api/items",
                    lambda request: {},
                    methods=("POST",),
                    request_schema=request_schema,
                    responses={201: {"description": "Created.", "schema": response_schema}, 400: {"description": "Bad input.", "schema": ERROR_SCHEMA}},
                )
            ]
        )
        operation = document["paths"]["/api/items"]["post"]
        self.assertEqual(operation["requestBody"]["content"]["application/json"]["schema"], request_schema)
        self.assertTrue(operation["requestBody"]["required"])
        self.assertEqual(operation["responses"]["201"]["content"]["application/json"]["schema"], response_schema)
        self.assertEqual(operation["responses"]["400"]["description"], "Bad input.")
        # An explicit 400 in `responses` is not overwritten by the automatic fallback.
        self.assertNotIn("200", operation["responses"])

    def test_post_without_declared_responses_still_gets_an_automatic_400(self):
        document = openapi_document([api_route("api/items", lambda request: {}, methods=("POST",))])
        self.assertIn("400", document["paths"]["/api/items"]["post"]["responses"])

    def test_custom_title_and_version_are_used(self):
        document = openapi_document([], title="Bookstore API", version="2.1.0")
        self.assertEqual(document["info"]["title"], "Bookstore API")
        self.assertEqual(document["info"]["version"], "2.1.0")


class CreateApplicationDocumentationTests(unittest.TestCase):
    def test_api_title_and_version_flow_through_to_the_served_document(self):
        app = create_application([api_route("api/health", lambda request: {"status": "ok"})], api_title="Bookstore API", api_version="2.1.0")
        document = TestClient(app).get("/openapi.json").json()
        self.assertEqual(document["info"]["title"], "Bookstore API")
        self.assertEqual(document["info"]["version"], "2.1.0")


class AuthRoutesDocumentationTests(unittest.TestCase):
    def test_builtin_auth_routes_are_fully_described(self):
        app = create_application(auth_routes("users.sqlite3"))
        document = TestClient(app).get("/openapi.json").json()
        paths = document["paths"]

        login_post = paths["/api/auth/login"]["post"]
        self.assertEqual(set(login_post["requestBody"]["content"]["application/json"]["schema"]["required"]), {"username", "password"})
        self.assertIn("200", login_post["responses"])
        self.assertIn("401", login_post["responses"])

        me_get = paths["/api/auth/me"]["get"]
        self.assertEqual(me_get["security"], [{"bearerAuth": []}])

        sessions_get = paths["/api/auth/sessions"]["get"]
        self.assertEqual(sessions_get["responses"]["200"]["content"]["application/json"]["schema"]["type"], "array")

        reset_request_post = paths["/api/auth/password-reset/request"]["post"]
        self.assertNotIn("security", reset_request_post)
        self.assertIn("202", reset_request_post["responses"])
