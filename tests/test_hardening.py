import unittest
import logging

from mangoo import HardeningOptions, Response, path
from mangoo.hardening import RateLimiter
from mangoo.logging import JsonFormatter
from mangoo.runtime import create_application


def call(application, request_path="/", *, method="GET", headers=None, remote_addr="127.0.0.1", scheme="http", content_length=None, query_string=""):
    captured = {}
    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = dict(response_headers)
    environ = {"PATH_INFO": request_path, "REQUEST_METHOD": method, "REMOTE_ADDR": remote_addr, "wsgi.url_scheme": scheme, "QUERY_STRING": query_string}
    if content_length is not None:
        environ["CONTENT_LENGTH"] = str(content_length)
    environ.update({f"HTTP_{key.upper().replace('-', '_')}": value for key, value in (headers or {}).items()})
    return captured, b"".join(application(environ, start_response))


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.options = HardeningOptions(allowed_hosts=("example.test",), allowed_origins=("https://app.example.test",), rate_limit=2, rate_window_seconds=60)
        self.application = create_application([path("", lambda request: Response("ok"))], hardening=self.options)

    def test_rejects_untrusted_host(self):
        captured, _ = call(self.application, headers={"Host": "attacker.test"})
        self.assertEqual(captured["status"], "400 Bad Request")

    def test_sets_security_headers_and_allowed_cors_origin(self):
        captured, _ = call(self.application, headers={"Host": "example.test", "Origin": "https://app.example.test"})
        self.assertEqual(captured["headers"]["X-Content-Type-Options"], "nosniff")
        self.assertEqual(captured["headers"]["Access-Control-Allow-Origin"], "https://app.example.test")
        self.assertIn("Content-Security-Policy", captured["headers"])
        self.assertEqual(captured["headers"]["Cross-Origin-Opener-Policy"], "same-origin")

    def test_handles_trusted_cors_preflight(self):
        captured, body = call(self.application, method="OPTIONS", headers={"Host": "example.test", "Origin": "https://app.example.test"})
        self.assertEqual(captured["status"], "204 No Content")
        self.assertEqual(body, b"")

    def test_rate_limiter_returns_retry_after(self):
        headers = {"Host": "example.test"}
        call(self.application, headers=headers, remote_addr="203.0.113.7")
        call(self.application, headers=headers, remote_addr="203.0.113.7")
        captured, _ = call(self.application, headers=headers, remote_addr="203.0.113.7")
        self.assertEqual(captured["status"], "429 Too Many Requests")
        self.assertEqual(captured["headers"]["Retry-After"], "60")

    def test_ignores_forwarded_for_without_trusted_proxy_mode(self):
        headers = {"Host": "example.test", "X-Forwarded-For": "198.51.100.3"}
        call(self.application, headers=headers, remote_addr="203.0.113.7")
        call(self.application, headers={**headers, "X-Forwarded-For": "198.51.100.4"}, remote_addr="203.0.113.7")
        captured, _ = call(self.application, headers={**headers, "X-Forwarded-For": "198.51.100.5"}, remote_addr="203.0.113.7")
        self.assertEqual(captured["status"], "429 Too Many Requests")

    def test_rejects_oversized_request_before_body_read(self):
        captured, _ = call(self.application, headers={"Host": "example.test"}, content_length=1_048_577)
        self.assertEqual(captured["status"], "413 Payload Too Large")

    def test_https_policy_redirects_and_sets_hsts(self):
        options = HardeningOptions(allowed_hosts=("example.test",), rate_limit=10, force_https=True)
        application = create_application([path("", lambda request: Response("ok"))], hardening=options)
        redirected, _ = call(application, headers={"Host": "example.test"})
        secure, _ = call(application, headers={"Host": "example.test"}, scheme="https")
        self.assertEqual(redirected["status"], "308 Permanent Redirect")
        self.assertEqual(redirected["headers"]["Location"], "https://example.test/")
        self.assertIn("max-age=31536000", secure["headers"]["Strict-Transport-Security"])

    def test_https_redirect_preserves_query_string(self):
        options = HardeningOptions(allowed_hosts=("example.test",), rate_limit=10, force_https=True)
        application = create_application([path("search", lambda request: Response("ok"))], hardening=options)
        redirected, _ = call(application, "/search", headers={"Host": "example.test"}, query_string="q=mangoo&page=2")
        self.assertEqual(redirected["headers"]["Location"], "https://example.test/search?q=mangoo&page=2")

    def test_forwarded_headers_require_an_explicit_trusted_proxy(self):
        options = HardeningOptions(allowed_hosts=("example.test",), rate_limit=10, force_https=True, trust_proxy_headers=True, trusted_proxy_ips=("10.0.0.0/8",))
        application = create_application([path("", lambda request: Response("ok"))], hardening=options)
        untrusted, _ = call(application, headers={"Host": "example.test", "X-Forwarded-Proto": "https"}, remote_addr="203.0.113.8")
        trusted, _ = call(application, headers={"Host": "example.test", "X-Forwarded-Proto": "https"}, remote_addr="10.2.3.4")
        self.assertEqual(untrusted["status"], "308 Permanent Redirect")
        self.assertEqual(trusted["status"], "200 OK")

    def test_audit_log_excludes_request_headers(self):
        with self.assertLogs("mangoo.audit", "INFO") as logs:
            call(self.application, headers={"Host": "example.test", "Authorization": "Bearer secret"})
        self.assertIn("method=GET path=/ status=200", logs.output[0])
        self.assertNotIn("secret", logs.output[0])

    def test_rate_limiter_bounds_client_state_and_evicts_least_recent_client(self):
        limiter = RateLimiter(limit=2, window_seconds=60, max_clients=2)
        limiter.allow("first", now=1)
        limiter.allow("second", now=2)
        limiter.allow("third", now=3)
        self.assertEqual(limiter.stats(), {"tracked_clients": 2, "max_clients": 2})
        self.assertTrue(limiter.allow("first", now=4))  # first was the LRU entry and was evicted

    def test_rate_limiter_prunes_expired_clients(self):
        limiter = RateLimiter(limit=1, window_seconds=10, max_clients=2)
        limiter.allow("old", now=0)
        limiter.allow("new", now=11)
        self.assertEqual(limiter.stats()["tracked_clients"], 1)

    def test_json_formatter_includes_request_metadata_without_headers(self):
        record = logging.LogRecord("mangoo.audit", logging.INFO, __file__, 0, "request complete", (), None)
        record.request_id, record.method, record.path, record.status, record.duration_ms = "request-1", "GET", "/healthz", 200, 2.5
        output = JsonFormatter().format(record)
        self.assertIn('"request_id":"request-1"', output)
        self.assertIn('"duration_ms":2.5', output)
        self.assertNotIn("Authorization", output)
