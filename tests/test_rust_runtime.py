"""Functional parity tests for hayavega.rust_runtime: the same application,
served over the native Rust WSGI server instead of wsgiref, should behave
identically to the existing Python runtime for every route this suite
exercises. Requires hayavega_native -- see the native-extension CI job."""

import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

try:
    import hayavega_native  # noqa: F401

    NATIVE_AVAILABLE = True
except ImportError:
    NATIVE_AVAILABLE = False

from hayavega import Integer, Text, api_route, path
from hayavega.runtime import create_application
from hayavega.rust_runtime import RustRuntimeUnavailableError
from hayavega.rust_runtime import serve as serve_rust
from hayavega.testing import TestClient


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_ready(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://{host}:{port}/__nonexistent__", timeout=0.5)
            return
        except urllib.error.HTTPError:
            return  # server answered, even with a 404 -- it's up
        except Exception as error:  # noqa: BLE001 -- polling until the socket accepts connections
            last_error = error
            time.sleep(0.05)
    raise RuntimeError(f"Rust server on {host}:{port} never became ready: {last_error}")


@unittest.skipUnless(NATIVE_AVAILABLE, "hayavega_native is not built; see the native-extension CI job")
class RustRuntimeParityTests(unittest.TestCase):
    """Same urlpatterns, same app -- one served by hayavega.runtime.create_application
    via TestClient (the existing, trusted WSGI path), one served by the real
    Rust server over a real socket. Every assertion compares the two."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.database = Path(cls.directory.name) / "rust_runtime.sqlite3"

        from hayavega import Entity

        class Post(Entity):
            title = Text()
            views = Integer()

        cls.Post = Post
        Post.collection(cls.database).append(title="Hello", views=3)

        def echo(request):
            return {"status": "ok"}

        def posts(request):
            return Post.collection(cls.database).browse().arrange("id").values("title", "views")

        cls.urlpatterns = [
            api_route("echo", echo, methods=("GET",)),
            path("posts", posts, methods=("GET",)),
        ]

        cls.host = "127.0.0.1"
        cls.port = _free_port()
        cls.server_thread = threading.Thread(target=lambda: serve_rust(cls.urlpatterns, cls.host, cls.port, workers=4), daemon=True)
        cls.server_thread.start()
        _wait_until_ready(cls.host, cls.port)

        cls.python_client = TestClient(create_application(cls.urlpatterns))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def _get(self, path_: str):
        return urllib.request.urlopen(f"http://{self.host}:{self.port}{path_}", timeout=5)

    def test_echo_route_matches_the_python_runtime(self):
        rust_response = self._get("/echo")
        python_response = self.python_client.get("/echo")
        self.assertEqual(rust_response.status, python_response.status_code)
        self.assertEqual(rust_response.read(), python_response.content)

    def test_db_backed_route_matches_the_python_runtime(self):
        rust_response = self._get("/posts")
        python_response = self.python_client.get("/posts")
        self.assertEqual(rust_response.status, python_response.status_code)
        self.assertEqual(rust_response.read(), python_response.content)

    def test_404_matches_the_python_runtime(self):
        with self.assertRaises(urllib.error.HTTPError) as rust_ctx:
            self._get("/nope")
        python_response = self.python_client.get("/nope")
        self.assertEqual(rust_ctx.exception.code, python_response.status_code)

    def test_query_string_is_parsed_correctly(self):
        def echo_query(request):
            return {"query": request.raw_query}

        urlpatterns = [path("q", echo_query, methods=("GET",))]
        port = _free_port()
        thread = threading.Thread(target=lambda: serve_rust(urlpatterns, self.host, port, workers=2), daemon=True)
        thread.start()
        _wait_until_ready(self.host, port)
        response = urllib.request.urlopen(f"http://{self.host}:{port}/q?a=1&b=2", timeout=5)
        self.assertIn(b"a=1&b=2", response.read())


class RustRuntimeUnavailableTests(unittest.TestCase):
    def test_serve_raises_an_actionable_error_when_hayavega_native_is_missing(self):
        if NATIVE_AVAILABLE:
            self.skipTest("hayavega_native is built in this environment; this test targets the fallback path")
        with self.assertRaisesRegex(RustRuntimeUnavailableError, "hayavega_native was not built"):
            serve_rust([], "127.0.0.1", _free_port())

    def test_serve_raises_an_actionable_error_when_hayavega_native_import_fails(self):
        """`serve()` imports `hayavega_native` inside the function body on every
        call (not just at module load), so forcing the ImportError just needs
        `sys.modules` patched for this one call -- no module reload, and
        therefore no risk of leaking a stale module state to other tests."""
        original = sys.modules.get("hayavega_native", None)
        sys.modules["hayavega_native"] = None  # sentinel: forces ImportError on import
        try:
            with self.assertRaisesRegex(RustRuntimeUnavailableError, "hayavega_native was not built"):
                serve_rust([], "127.0.0.1", _free_port())
        finally:
            if original is None:
                sys.modules.pop("hayavega_native", None)
            else:
                sys.modules["hayavega_native"] = original


if __name__ == "__main__":
    unittest.main()
