# Testing

`TestClient` and `AsyncTestClient` drive your real application in-process
&mdash; no server, no sockets, no network flakiness. This is the exact
tooling Mangoo's own 415+ tests run on.

## WSGI: `TestClient`

```python
import unittest
from mangoo.runtime import create_application
from mangoo.testing import TestClient
from .urls import urlpatterns


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_application(urlpatterns))

    def test_list_posts(self):
        response = self.client.get("posts")
        self.assertEqual(response.status_code, 200)
        self.assertIn("title", response.json()[0])

    def test_create_post_requires_csrf(self):
        response = self.client.post("posts", form_data={"title": "Hello"})
        self.assertEqual(response.status_code, 403)
```

`TestResponse` gives you `.status_code`, `.headers`, `.content` (bytes),
`.text`, and `.json()`. `.get(path, **kwargs)`/`.post(path, **kwargs)`
accept `json_data=`, `form_data=`, and `headers=`; cookies (session
tokens, CSRF) are tracked automatically across requests on the same
client instance.

## ASGI: `AsyncTestClient`

```python
from mangoo.asgi import create_asgi_application
from mangoo.testing import AsyncTestClient

client = AsyncTestClient(create_asgi_application(urlpatterns))
response = client.get("/api/items")  # synchronous from the caller's perspective
```

Drives a real ASGI 3.0 `scope`/`receive`/`send` cycle, one `asyncio.run()`
per call &mdash; no pytest-async plugin required.

## Testing the Rust runtime

Requires `mangoo_native` to be built. Since it's a real server (not a
WSGI-callable-in-process client), tests hit it over a real socket:

```python
import socket, threading, time
from mangoo.rust_runtime import serve

thread = threading.Thread(target=lambda: serve(urlpatterns, "127.0.0.1", port, workers=4), daemon=True)
thread.start()
# ... poll until the port accepts connections, then use urllib/socket directly
```

See `tests/test_rust_runtime.py` (functional parity against `TestClient`)
and `tests/test_rust_runtime_hardening.py` (adversarial requests over raw
sockets &mdash; malformed `Content-Length`, chunked encoding, CRLF
injection) for the real pattern this project uses.

## Database fixtures

Most model tests use a real, temporary SQLite file per test case rather
than mocking the database layer:

```python
import tempfile
from pathlib import Path


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "test.sqlite3"

    def tearDown(self):
        self.directory.cleanup()
```

PostgreSQL and MySQL/MariaDB integration tests are opt-in and run against
real service containers in CI, not mocks &mdash; see `tests/test_postgresql_integration.py`
and `tests/test_mysql_integration.py`.

## Running the suite

```bash
pytest                    # full suite, coverage-gated
ruff check . && ruff format --check .
mypy
```
