# REST & OpenAPI

`api_route()` views generate a real OpenAPI document automatically &mdash;
the same relationship many modern API frameworks' route decorators have
to their own generated schema.

## Basic usage

```python
from hayavega import api_route


def get_post(request):
    return {"id": 1, "title": "Hello"}


urlpatterns = [
    api_route("posts/1", get_post, methods=("GET",)),
]
```

Visit `/openapi.json` (the generated document) or `/docs` (a Swagger UI
serving it) &mdash; both wired up automatically by `create_application()`.

## Describing an endpoint fully

```python
api_route(
    "posts",
    create_post,
    methods=("POST",),
    secured=True,
    request_schema={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    responses={
        201: {"description": "Post created", "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
        400: {"description": "Validation failed"},
    },
)
```

None of `secured`/`request_schema`/`responses` change runtime behavior &mdash;
they only shape the generated document. `secured=True` adds the
`bearerAuth` security requirement, backed by a `bearerAuth` scheme every
generated document includes.

## Naming and versioning the document

```python
from hayavega.runtime import create_application

application = create_application(urlpatterns, api_title="My API", api_version="1.0.0")
```

## Error envelope

Every built-in error response (and the convention this project's own
error paths follow) uses `{"detail": string}`, documented via a shared
`Error` schema referenced everywhere a non-2xx response can occur. Path
versioning follows a `api/v1/...` prefix convention &mdash; a plain
convention, not framework-enforced, documented so every project in an
organization can agree on it.

## Metrics

```python
from hayavega.metrics import MetricsRegistry, metrics_routes

registry = MetricsRegistry()
application = create_application(urlpatterns, metrics=registry)
urlpatterns += metrics_routes(registry)  # exposes /metrics in Prometheus text format
```

Opt-in (`metrics=None` is the default and skips every metrics call
entirely), recording per-request counts and a latency histogram labeled
by matched route pattern and status.

## Error disclosure

A resolver/view exception never leaks its raw message to an anonymous
HTTP client &mdash; only `error_type` (e.g. `"RuntimeError"`) is exposed;
the full exception with traceback is always logged server-side. This
closes a real, previously-found gap (a database connection-failure
message routinely embeds host/port/username) &mdash; see
[Security](security.md) for the full error-disclosure standard this
project holds every request path to, not only REST's.
