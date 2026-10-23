import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_hardening import call

from mangoo import health_routes
from mangoo.production import ProductionConfigurationError, validate_production
from mangoo.runtime import create_application


class ProductionTests(unittest.TestCase):
    def test_production_validation_rejects_insecure_defaults(self):
        settings = SimpleNamespace(debug=False, secret_key="change-this-in-production", allowed_hosts=(), security={})
        with self.assertRaisesRegex(ProductionConfigurationError, "SECRET_KEY"):
            validate_production(settings)

    def test_production_validation_allows_explicit_safe_configuration(self):
        settings = SimpleNamespace(debug=False, secret_key="not-a-default-secret", allowed_hosts=("app.example.test",), security={"FORCE_HTTPS": True, "SECURE_COOKIES": True})
        validate_production(settings)

    def test_health_and_readiness_report_database_state(self):
        application = create_application(health_routes(":memory:"))
        health, health_body = call(application, "/healthz")
        ready, ready_body = call(application, "/readyz")
        self.assertEqual(health["status"], "200 OK")
        self.assertEqual(health_body, b'{"status":"ok"}')
        self.assertEqual(ready["status"], "200 OK")
        self.assertEqual(ready_body, b'{"status":"ok","database":"ok"}')

    def test_readiness_reports_unavailable_when_the_database_connection_fails(self):
        with patch("mangoo.production.connect", side_effect=RuntimeError("connection refused")):
            application = create_application(health_routes(":memory:"))
            ready, ready_body = call(application, "/readyz")
        self.assertTrue(ready["status"].startswith("503"))
        self.assertEqual(ready_body, b'{"status":"unavailable","database":"unavailable"}')
