# Middleware

A middleware wraps route matching and the view call with your own
request/response processing &mdash; logging, a custom auth check, request
timing, anything that should run around every view without decorating
each one individually.

## Writing a middleware

A middleware is a factory: it takes `get_response` (the next thing in
the chain -- either another middleware or the final view dispatch) and
returns a handler that takes a `request` and returns a `Response`.

```python title="myapp/middleware.py"
import time


def timing_middleware(get_response):
    def handler(request):
        started = time.perf_counter()
        response = get_response(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"{request.method} {request.path} -- {elapsed_ms:.1f}ms")
        return response

    return handler
```

A middleware can also short-circuit by never calling `get_response`:

```python
def maintenance_mode(get_response):
    def handler(request):
        if request.path == "healthz":
            return get_response(request)
        return Response("Down for maintenance", status=503)

    return handler
```

## Registering middleware

List dotted paths in `Settings.middleware`, same convention as
`Settings.job_handlers`:

```python title="config/settings.py"
class Settings:
    middleware = [
        "myapp.middleware.timing_middleware",
    ]
```

`config/wsgi.py`/`config/asgi.py` already resolve and pass this list for
you (`mangoo.middleware.load_middleware(settings.middleware)` into
`create_application(..., middleware=...)` /
`create_asgi_application(..., middleware=...)`).

## Ordering

The **first** entry in the list is the **outermost** wrapper -- it sees
the request first and the response last. The **last** entry is
innermost, closest to the view. With
`middleware = ["myapp.a", "myapp.b"]`, a request flows `a -> b -> view`,
then the response flows back `view -> b -> a`.

## Where it sits in the pipeline

Middleware runs **after** hardening (trusted-host checks, rate
limiting, CORS, request-size limits) and **before** the matched view.
Hardening always runs first and can't be bypassed by a middleware --
unlike Django, where security middleware is just another entry in the
same list, Mangoo keeps that layer separate and non-optional on
purpose.

## Async

Under ASGI, a middleware's handler must be `async def` to wrap an async
`get_response`. If you need one middleware that works under both WSGI
and ASGI, check `inspect.iscoroutinefunction(get_response)` and return
the matching kind of handler -- the same pattern `csrf_protect`/
`permission_required` already use internally to support both sync and
async views.
