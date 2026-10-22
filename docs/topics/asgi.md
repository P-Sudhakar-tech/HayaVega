# ASGI

WSGI is the default for every scaffolded project and needs nothing extra.
Reach for ASGI when a view needs `async def` for concurrent I/O (calling
another service, an async database driver) that WSGI's
one-thread-per-request model can't overlap as cheaply.

## Setup

```python title="config/asgi.py"
from mangoo.asgi import create_asgi_application
from .routes import urlpatterns

application = create_asgi_application(urlpatterns, api_title="My API", api_version="1.0.0")
```

```bash
uvicorn config.asgi:application --host 0.0.0.0 --port 8000
```

`mango newproject` scaffolds this file automatically alongside
`config/wsgi.py`.

## Sync and async views, unmodified

```python
async def fetch_remote(request):
    async with httpx.AsyncClient() as client:
        data = (await client.get("https://api.example.com")).json()
    return data


def existing_sync_view(request):
    return {"status": "ok"}  # unchanged, works under either transport
```

Sync views run on a dedicated worker-thread pool
(`max_sync_workers=` on `create_asgi_application`, default 16, matching
this project's own documented `waitress --threads=16` guidance) &mdash;
not `asyncio`'s shared, CPU-offloading-sized default executor, which was
measured as a real concurrency bottleneck under realistic load (see
[Performance](../performance.md)).

## Decorators compose correctly with async views

`api_route`, `csrf_protect`, `login_required`, `superuser_required`, and
`email_verified_required` all detect an `async def` view
(`inspect.iscoroutinefunction`) and produce a matching async wrapper, so
the ASGI runtime awaits it directly instead of routing it through the
thread pool. This closed a real gap found while building ASGI support:
wrapping an async view with any of these previously produced a silently
unawaited coroutine instead of a `Response`.

## WebSockets

ASGI also carries `websocket` scopes, not just `http` ones -- pass
`create_asgi_application(websocket_patterns=[...])` to add them. See
[WebSockets](websockets.md) for the handler contract, hijacking
protection, and testing.

## Testing

```python
from mangoo.testing import AsyncTestClient
from mangoo.asgi import create_asgi_application

client = AsyncTestClient(create_asgi_application(urlpatterns))
response = client.get("/api/items")
```

Drives a real ASGI 3.0 `scope`/`receive`/`send` cycle in-process,
synchronously from the caller's perspective &mdash; no pytest-async plugin
required, matching this project's `unittest`-based style.

## Why choose WSGI vs ASGI

Mangoo's own [published benchmarks](../performance.md) show ASGI winning
or tying every comparable framework measured on every scenario &mdash; but
none of those scenarios exercise the concurrent I/O that's the actual
reason to choose ASGI over WSGI in a real project. Choose ASGI when you have `async def`
views doing real concurrent I/O; stay on WSGI otherwise, since it's
simpler and remains the framework's default for a reason.
