"""Verifies the hand-written Rust HTTP/1.1 server actually rejects the
known failure modes of a hand-rolled server -- request smuggling via
Transfer-Encoding/malformed Content-Length, response splitting via header
injection, and that a client that appears to malform a request does not
corrupt a keep-alive connection for the request that follows. Talks to the
real server over a real raw socket (not urllib, which would not let us
send deliberately malformed requests) -- requires hayavega_native."""

import socket
import threading
import time
import unittest

try:
    import hayavega_native  # noqa: F401

    NATIVE_AVAILABLE = True
except ImportError:
    NATIVE_AVAILABLE = False

from hayavega import api_route, path
from hayavega.response import Response
from hayavega.rust_runtime import serve as serve_rust


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_ready(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except Exception as error:  # noqa: BLE001 -- polling until the socket accepts connections
            last_error = error
            time.sleep(0.05)
    raise RuntimeError(f"Rust server on {host}:{port} never became ready: {last_error}")


def _reflect_header(request):
    # Reflects a query parameter straight into a response header -- exactly
    # the shape of application code (echoing a redirect target, a
    # client-supplied label, ...) that a CRLF-carrying value could turn
    # into HTTP response splitting if this server didn't defend against it
    # itself. `%0d%0a` in the query string decodes to real CR/LF bytes by
    # the time this view sees it (standard URL decoding), the same way it
    # would for any real application reflecting user input this way.
    value = request.query_params.get("v", [""])[0]
    return Response("ok", headers=(("X-Echo", value),))


@unittest.skipUnless(NATIVE_AVAILABLE, "hayavega_native is not built; see the native-extension CI job")
class RustRuntimeHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def echo(request):
            return {"status": "ok"}

        urlpatterns = [
            api_route("echo", echo, methods=("GET",)),
            path("echo-header", _reflect_header, methods=("GET",)),
        ]
        cls.host = "127.0.0.1"
        cls.port = _free_port()
        cls.server_thread = threading.Thread(target=lambda: serve_rust(urlpatterns, cls.host, cls.port, workers=4), daemon=True)
        cls.server_thread.start()
        _wait_until_ready(cls.host, cls.port)

    def _raw_request(self, request_bytes: bytes, *, read_bytes: int = 65536, timeout: float = 3.0) -> bytes:
        with socket.create_connection((self.host, self.port), timeout=timeout) as sock:
            sock.sendall(request_bytes)
            sock.settimeout(timeout)
            chunks = []
            try:
                while True:
                    chunk = sock.recv(read_bytes)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except TimeoutError:
                pass
            return b"".join(chunks)

    def test_content_length_and_transfer_encoding_together_is_rejected(self):
        request = b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\nhello"
        response = self._raw_request(request)
        self.assertIn(b"400", response.split(b"\r\n", 1)[0])

    def test_chunked_transfer_encoding_alone_is_rejected_not_silently_misparsed(self):
        request = b"POST /echo HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
        response = self._raw_request(request)
        status_line = response.split(b"\r\n", 1)[0]
        self.assertNotIn(b"200", status_line)

    def test_malformed_content_length_is_rejected_not_silently_treated_as_zero(self):
        request = b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: not-a-number\r\n\r\n"
        response = self._raw_request(request)
        self.assertIn(b"400", response.split(b"\r\n", 1)[0])

    def test_a_malformed_request_does_not_corrupt_the_next_connection(self):
        """The server must not be left in a state where one bad connection's
        unread bytes desync a *different, later* connection -- each
        connection has its own BufReader, so this should already hold, but
        prove it rather than assume it."""
        malformed = b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: not-a-number\r\n\r\n"
        self._raw_request(malformed)

        with socket.create_connection((self.host, self.port), timeout=3.0) as sock:
            sock.sendall(b"GET /echo HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            sock.settimeout(3.0)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            response = b"".join(chunks)
        self.assertIn(b"200", response.split(b"\r\n", 1)[0])

    def test_a_response_header_containing_crlf_cannot_split_the_response(self):
        """A view reflecting user input into a header value is real,
        common application code -- this server, not just the application,
        must be the backstop against HTTP response splitting."""
        request = b"GET /echo-header?v=evil%0d%0aX-Injected:%20yes HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
        response = self._raw_request(request)
        status_line = response.split(b"\r\n", 1)[0]
        self.assertIn(b"500", status_line)
        self.assertNotIn(b"X-Injected", response)

    def test_keep_alive_connection_survives_multiple_requests(self):
        with socket.create_connection((self.host, self.port), timeout=3.0) as sock:
            sock.sendall(b"GET /echo HTTP/1.1\r\nHost: x\r\n\r\n")
            sock.settimeout(3.0)
            first = sock.recv(65536)
            self.assertIn(b"200", first.split(b"\r\n", 1)[0])
            self.assertIn(b"keep-alive", first.lower())

            sock.sendall(b"GET /echo HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            second = sock.recv(65536)
            self.assertIn(b"200", second.split(b"\r\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
