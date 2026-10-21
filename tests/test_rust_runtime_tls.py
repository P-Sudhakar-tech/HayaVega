"""Verifies mangoo.rust_runtime's optional TLS termination actually serves
HTTPS, not just that the code compiles: a real client TLS handshake
against a real self-signed certificate, over a real socket. Requires
mangoo_native and the `openssl` CLI (used only to generate a throwaway
self-signed test certificate/key pair -- not a project dependency, and
the generated files are never committed)."""

import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType

try:
    import mangoo_native  # noqa: F401

    NATIVE_AVAILABLE = True
except ImportError:
    NATIVE_AVAILABLE = False

from mangoo.conf import load_settings_from_module
from mangoo.rust_runtime import serve as serve_rust

OPENSSL_AVAILABLE = shutil.which("openssl") is not None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_https_ready(host: str, port: int, ssl_context: ssl.SSLContext, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5) as raw:
                with ssl_context.wrap_socket(raw, server_hostname=host):
                    return
        except Exception as error:  # noqa: BLE001 -- polling until the TLS handshake succeeds
            last_error = error
            time.sleep(0.05)
    raise RuntimeError(f"HTTPS server on {host}:{port} never became ready: {last_error}")


@unittest.skipUnless(NATIVE_AVAILABLE, "mangoo_native is not built; see the native-extension CI job")
@unittest.skipUnless(OPENSSL_AVAILABLE, "openssl CLI is not on PATH; used only to generate a throwaway test certificate")
class RustRuntimeTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.cert_path = Path(cls.directory.name) / "test-cert.pem"
        cls.key_path = Path(cls.directory.name) / "test-key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(cls.key_path),
                "-out",
                str(cls.cert_path),
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=localhost",
            ],
            check=True,
            capture_output=True,
        )

        def echo(request):
            return {"scheme": request.is_secure}

        from mangoo import api_route

        urlpatterns = [api_route("echo", echo, methods=("GET",))]
        cls.host = "127.0.0.1"
        cls.port = _free_port()
        cls.server_thread = threading.Thread(
            target=lambda: serve_rust(urlpatterns, cls.host, cls.port, workers=4, tls_cert=str(cls.cert_path), tls_key=str(cls.key_path)),
            daemon=True,
        )
        cls.server_thread.start()

        cls.client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cls.client_context.check_hostname = False
        cls.client_context.verify_mode = ssl.CERT_NONE  # a throwaway self-signed test cert is never trusted by default
        _wait_until_https_ready(cls.host, cls.port, cls.client_context)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_https_handshake_succeeds_and_serves_the_real_application(self):
        with socket.create_connection((self.host, self.port), timeout=5) as raw:
            with self.client_context.wrap_socket(raw, server_hostname=self.host) as tls_sock:
                tls_sock.sendall(b"GET /echo HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                response = b""
                while True:
                    chunk = tls_sock.recv(65536)
                    if not chunk:
                        break
                    response += chunk
        self.assertIn(b"200", response.split(b"\r\n", 1)[0])
        self.assertIn(b'"scheme":true', response.replace(b" ", b""))

    def test_plain_http_to_an_https_port_does_not_crash_the_server(self):
        """A client that connects without TLS (or a portscanner, or a
        misconfigured health check) must fail the connection cleanly, not
        take the server down or hang the worker thread forever."""
        with socket.create_connection((self.host, self.port), timeout=5) as raw:
            raw.settimeout(5)
            raw.sendall(b"GET /echo HTTP/1.1\r\nHost: localhost\r\n\r\n")
            try:
                raw.recv(1024)
            except (TimeoutError, ConnectionResetError, OSError):
                pass  # any of these is an acceptable "the handshake failed" outcome

        # The server itself must still be alive and answering real HTTPS
        # requests afterward -- prove it, not assume it.
        with socket.create_connection((self.host, self.port), timeout=5) as raw:
            with self.client_context.wrap_socket(raw, server_hostname=self.host) as tls_sock:
                tls_sock.sendall(b"GET /echo HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                response = tls_sock.recv(65536)
        self.assertIn(b"200", response.split(b"\r\n", 1)[0])


class TlsSettingsValidationTests(unittest.TestCase):
    def _module(self, server: dict) -> ModuleType:
        from types import SimpleNamespace

        module = ModuleType("config.settings")
        module.settings = SimpleNamespace(database={"engine": "sqlite3", "name": "mangoo.sqlite3"}, server=server)
        return module

    def test_tls_cert_without_tls_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "tls_cert"):
            load_settings_from_module(self._module({"engine": "rust", "tls_cert": "cert.pem"}))

    def test_tls_settings_under_the_python_engine_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "rust"):
            load_settings_from_module(self._module({"engine": "python", "tls_cert": "cert.pem", "tls_key": "key.pem"}))

    def test_tls_settings_under_the_rust_engine_are_accepted(self):
        settings = load_settings_from_module(self._module({"engine": "rust", "tls_cert": "cert.pem", "tls_key": "key.pem"}))
        self.assertEqual(settings.server_tls_cert, "cert.pem")
        self.assertEqual(settings.server_tls_key, "key.pem")


if __name__ == "__main__":
    unittest.main()
