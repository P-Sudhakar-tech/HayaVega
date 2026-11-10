# Routing

Every Hayavega route maps a URL pattern to a plain Python function taking a
`Request` and returning a `Response` (or, for `api_route`, anything
JSON-serializable).

## `path()` &mdash; HTML and general views

```python
from hayavega import path


def home(request):
    return Response("<h1>Hello</h1>")


urlpatterns = [
    path("", home, methods=("GET",)),
    path("posts/<path>", post_detail, methods=("GET",)),  # trailing wildcard segment
]
```

`methods` defaults to `("GET",)`. A path ending in `/<path>` matches any
suffix, handed to the view via the request's path info &mdash; the same
mechanism `static_routes()` uses internally to serve a whole directory
tree under one route.

## `api_route()` &mdash; JSON views with automatic OpenAPI docs

```python
from hayavega import api_route


def list_items(request):
    return {"items": [...]}  # dict/list return values are JSON-encoded automatically


urlpatterns = [
    api_route("api/items", list_items, methods=("GET",), secured=True, responses={200: {"description": "A list of items"}}),
]
```

Unlike `path()`, a view wired with `api_route()` may return a plain
`dict`/`list` directly &mdash; it's wrapped in `json_response()`
automatically, or you can return a `Response` yourself when you need
custom headers or a non-200 status.

`secured=True`, `request_schema=`, and `responses=` don't change runtime
behavior at all &mdash; they only feed the generated OpenAPI document at
`/openapi.json` and the Swagger UI at `/docs`. See
[REST & OpenAPI](rest-api.md) for the full documentation story.

## Async views

Both `path()` and `api_route()` views may be `async def` when served under
[ASGI](asgi.md). Every built-in decorator (`csrf_protect`,
`login_required`, `permission_required`, `api_route` itself) detects an
`async def` view via `inspect.iscoroutinefunction` and produces a matching
async wrapper, so composing them with async views works exactly like with
sync ones &mdash; see `tests/test_asgi.py`'s
`test_login_required_composes_with_an_async_view` for the exact contract
this guarantees.

## Static files

```python
from hayavega import static_routes
from hayavega.conf import load_settings

_settings = load_settings()

urlpatterns = [
    *static_routes(_settings.base_dir / "static", "static"),
]
```

Serves an entire directory with correct MIME types, `ETag`/conditional
requests, and cache-control headers &mdash; the same static-serving
machinery [the benchmark suite](../performance.md) measures directly
against other frameworks' own static handling.

## Request and Response

```python
from hayavega.request import Request
from hayavega.response import Response, json_response


def echo(request: Request) -> Response:
    return json_response(
        {
            "method": request.method,
            "path": request.path,
            "query": request.query_params,
            "is_secure": request.is_secure,
        }
    )
```

`Request` is a frozen dataclass: `method`, `path`, `query_params`, `body`,
`headers`, `remote_addr`, `is_secure`, plus `.json()`, `.form()`, and
`.cookies`. `Response` is `body`/`status`/`content_type`/`headers`.

## 404s and route listing in debug mode

When `create_application(..., debug=True)`, an unmatched route's 404 page
lists every registered route pattern &mdash; computed once at
application-build time (free when a route *does* match, and free again
in production where `debug=False`), never leaking anything beyond your
own app's route table.

## Where routing plugs into everything else

- [REST & OpenAPI](rest-api.md) &mdash; `api_route`'s documentation surface
- [GraphQL](graphql.md) &mdash; `graphql_view(schema)` is itself just a
  `path()`-compatible view
- [Security](security.md) &mdash; `csrf_protect`, `login_required`, and
  friends compose with any view, sync or async
