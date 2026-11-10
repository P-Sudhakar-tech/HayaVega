import io
import json
import tempfile
import unittest
from pathlib import Path

from hayavega.audit import audit_events
from hayavega.auth import authenticate, create_session, create_user, list_sessions
from hayavega.auth_api import auth_routes
from hayavega.hardening import HardeningOptions, is_trusted_proxy_source, parse_trusted_proxy_networks, resolve_client_ip
from hayavega.runtime import create_application

PASSWORD = "Correct-Horse-7!"


def call(application, path, *, method="POST", body=None, remote_addr="203.0.113.1", user_agent=None, forwarded_for=None, token=None):
    captured = {}
    encoded_body = json.dumps(body).encode() if body is not None else b""

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "CONTENT_LENGTH": str(len(encoded_body)),
        "wsgi.input": io.BytesIO(encoded_body),
        "CONTENT_TYPE": "application/json",
        "REMOTE_ADDR": remote_addr,
    }
    if user_agent is not None:
        environ["HTTP_USER_AGENT"] = user_agent
    if forwarded_for is not None:
        environ["HTTP_X_FORWARDED_FOR"] = forwarded_for
    if token:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    body_bytes = b"".join(application(environ, start_response))
    return captured, (json.loads(body_bytes) if body_bytes else None)


class LoginTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "users.sqlite3"
        create_user(self.database, "alice", "alice@example.com", PASSWORD)

    def tearDown(self):
        self.directory.cleanup()

    def test_login_records_ip_and_user_agent_in_the_audit_log(self):
        app = create_application(auth_routes(self.database))
        call(app, "/api/auth/login", body={"username": "alice", "password": PASSWORD}, remote_addr="198.51.100.7", user_agent="ExampleBrowser/1.0")
        succeeded = [e for e in audit_events(self.database) if e["event_type"] == "auth.login_succeeded"][0]
        created = [e for e in audit_events(self.database) if e["event_type"] == "auth.session_created"][0]
        self.assertEqual(succeeded["metadata"], {"ip_address": "198.51.100.7", "user_agent": "ExampleBrowser/1.0"})
        self.assertEqual(created["metadata"], {"ip_address": "198.51.100.7", "user_agent": "ExampleBrowser/1.0"})

    def test_failed_login_also_records_ip(self):
        app = create_application(auth_routes(self.database))
        call(app, "/api/auth/login", body={"username": "alice", "password": "wrong"}, remote_addr="198.51.100.7")
        failed = [e for e in audit_events(self.database) if e["event_type"] == "auth.login_failed"][0]
        self.assertEqual(failed["metadata"]["ip_address"], "198.51.100.7")

    def test_session_listing_includes_ip_and_user_agent(self):
        app = create_application(auth_routes(self.database))
        _, login_body = call(app, "/api/auth/login", body={"username": "alice", "password": PASSWORD}, remote_addr="198.51.100.7", user_agent="ExampleBrowser/1.0")
        _, sessions = call(app, "/api/auth/sessions", method="GET", token=login_body["token"])
        self.assertEqual(sessions[0]["ip_address"], "198.51.100.7")
        self.assertEqual(sessions[0]["user_agent"], "ExampleBrowser/1.0")

    def test_untrusted_forwarded_for_is_ignored_without_hardening_configured(self):
        app = create_application(auth_routes(self.database))
        call(app, "/api/auth/login", body={"username": "alice", "password": PASSWORD}, remote_addr="203.0.113.1", forwarded_for="198.51.100.99")
        event = [e for e in audit_events(self.database) if e["event_type"] == "auth.login_succeeded"][0]
        self.assertEqual(event["metadata"]["ip_address"], "203.0.113.1")

    def test_forwarded_for_is_honored_only_from_a_trusted_proxy(self):
        hardening = HardeningOptions(trust_proxy_headers=True, trusted_proxy_ips=("10.0.0.0/8",))
        app = create_application(auth_routes(self.database, hardening=hardening))

        call(app, "/api/auth/login", body={"username": "alice", "password": PASSWORD}, remote_addr="10.1.1.1", forwarded_for="203.0.113.9")
        call(app, "/api/auth/login", body={"username": "alice", "password": PASSWORD}, remote_addr="203.0.113.50", forwarded_for="203.0.113.9")

        events = [e for e in audit_events(self.database) if e["event_type"] == "auth.login_succeeded"]
        # audit_events() returns newest first, so index 1 is the trusted-proxy call and index 0 is the untrusted one.
        self.assertEqual(events[1]["metadata"]["ip_address"], "203.0.113.9")  # trusted proxy: header honored
        self.assertEqual(events[0]["metadata"]["ip_address"], "203.0.113.50")  # untrusted source: header ignored

    def test_missing_user_agent_is_not_recorded_as_a_key(self):
        app = create_application(auth_routes(self.database))
        call(app, "/api/auth/login", body={"username": "alice", "password": PASSWORD}, remote_addr="203.0.113.1")
        event = [e for e in audit_events(self.database) if e["event_type"] == "auth.login_succeeded"][0]
        self.assertNotIn("user_agent", event["metadata"])


class AuthPrimitiveTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "users.sqlite3"
        self.user = create_user(self.database, "alice", "alice@example.com", PASSWORD)

    def tearDown(self):
        self.directory.cleanup()

    def test_authenticate_accepts_and_records_ip_and_user_agent(self):
        authenticate(self.database, "alice", PASSWORD, ip_address="203.0.113.1", user_agent="UA/1")
        event = [e for e in audit_events(self.database) if e["event_type"] == "auth.login_succeeded"][0]
        self.assertEqual(event["metadata"], {"ip_address": "203.0.113.1", "user_agent": "UA/1"})

    def test_create_session_stores_ip_and_user_agent_on_the_session(self):
        create_session(self.database, self.user, ip_address="203.0.113.1", user_agent="UA/1")
        summary = list_sessions(self.database, self.user)[0]
        self.assertEqual(summary.ip_address, "203.0.113.1")
        self.assertEqual(summary.user_agent, "UA/1")

    def test_session_without_telemetry_leaves_fields_none(self):
        create_session(self.database, self.user)
        summary = list_sessions(self.database, self.user)[0]
        self.assertIsNone(summary.ip_address)
        self.assertIsNone(summary.user_agent)


class ClientIpResolutionTests(unittest.TestCase):
    class FakeRequest:
        def __init__(self, remote_addr, headers=None):
            self.remote_addr = remote_addr
            self.headers = headers or {}

    def test_parse_trusted_proxy_networks_accepts_ips_and_cidrs(self):
        networks = parse_trusted_proxy_networks(("10.0.0.0/8", "192.168.1.1"))
        self.assertEqual(len(networks), 2)

    def test_parse_trusted_proxy_networks_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_trusted_proxy_networks(("not-an-ip",))

    def test_is_trusted_proxy_source_handles_invalid_remote_addr(self):
        networks = parse_trusted_proxy_networks(("10.0.0.0/8",))
        self.assertFalse(is_trusted_proxy_source("not-an-ip", networks))

    def test_is_trusted_proxy_source_false_with_no_networks_configured(self):
        self.assertFalse(is_trusted_proxy_source("10.1.1.1", ()))

    def test_resolve_client_ip_falls_back_to_unknown_without_remote_addr(self):
        request = self.FakeRequest("")
        self.assertEqual(resolve_client_ip(request, trust_proxy_headers=False, trusted_proxy_networks=()), "unknown")

    def test_resolve_client_ip_ignores_forwarded_header_when_not_trusting_proxies(self):
        request = self.FakeRequest("203.0.113.1", {"X-Forwarded-For": "198.51.100.1"})
        self.assertEqual(resolve_client_ip(request, trust_proxy_headers=False, trusted_proxy_networks=()), "203.0.113.1")

    def test_resolve_client_ip_uses_first_forwarded_address_from_a_trusted_proxy(self):
        networks = parse_trusted_proxy_networks(("10.0.0.0/8",))
        request = self.FakeRequest("10.1.1.1", {"X-Forwarded-For": "198.51.100.1, 10.1.1.1"})
        self.assertEqual(resolve_client_ip(request, trust_proxy_headers=True, trusted_proxy_networks=networks), "198.51.100.1")


if __name__ == "__main__":
    unittest.main()
