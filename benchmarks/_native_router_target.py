"""Driver used by native_router_vs_python.py to serve
benchmarks/hayavega_app's exact routes/views/seed data through a WSGI
application that uses hayavega_native.Router for route matching instead of
runtime.py's Python dict-based _match() closure -- everything else
(Request/Response construction, method checking, error handling) is
runtime.py's real, unmodified logic, so the only thing this isolates is
the matching logic itself. No Hardening/middleware here, matching
benchmarks/hayavega_app/app.py's own build_application(), which configures
neither.

This is a deliberate, scoped duplication of create_application()'s body
(runtime.py), not a reusable abstraction -- it exists only to answer
Phase 1's benchmark question (RUST_CORE_REWRITE_ROADMAP.md) and is not
shipped as a real framework option unless that benchmark justifies wiring
hayavega_native.Router into runtime.py/asgi.py for real. Also fixes the
double _match() call runtime.py's application() currently makes (once for
metrics labeling, once inside _dispatch) by reusing one match result for
both -- a correctness/efficiency improvement independent of Rust, folded
in here so the comparison is against a *correct* baseline, not an
artificially slow one.

Not a benchmark itself -- see native_router_vs_python.py.
"""

import html
import logging
import re
import sys
from pathlib import Path
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.hayavega_app.app import seed, urlpatterns  # noqa: E402
from hayavega.request import Request  # noqa: E402
from hayavega.response import Response  # noqa: E402
from hayavega.runtime import _STATUS_TEXT, _error_response  # noqa: E402
from hayavega.swagger import documentation_routes  # noqa: E402

LOGGER = logging.getLogger("hayavega.runtime")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def build_application():
    """A duplicate of runtime.create_application(), with _match() replaced
    by one hayavega_native.Router call and the duplicate match removed --
    see this module's docstring for why this is a benchmark-only
    duplication rather than a runtime.py change."""
    from hayavega_native import Router

    seed()
    route_list = [*urlpatterns]
    route_list.extend(documentation_routes(route_list, title="Benchmark (native router)", version="1.0.0"))
    router = Router([(route.pattern, route) for route in route_list])
    hardening_layer = None  # matches benchmarks/hayavega_app/app.py's build_application(), which passes no hardening=
    debug = False
    available_routes_html = ""

    def _dispatch(request, route) -> Response:
        if route is None:
            body = f"<h1>404 Not Found</h1><p>No route matches '/{html.escape(request.path.strip('/'))}'.</p>{available_routes_html}" if debug else "<h1>404 Not Found</h1>"
            return Response(body, status=404)
        if request.method not in route.methods:
            return Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", ", ".join(route.methods)),))
        try:
            result = route.view(request)
            return result if isinstance(result, Response) else Response(str(result))
        except Exception as exc:
            LOGGER.exception("Unhandled request error path=%s", request.path)
            return _error_response(route, exc, debug=debug)

    def application(environ, start_response):
        started_at = perf_counter()
        requested = environ.get("PATH_INFO", "/").strip("/")
        route = router.match_path(requested)  # matched once, reused below -- the duplicate-call fix noted in the module docstring
        try:
            content_length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            content_length = 0
        size_rejection = hardening_layer.reject_content_length(content_length) if hardening_layer else None
        request = Request.from_wsgi(environ, read_body=size_rejection is None)
        incoming_id = request.headers.get("X-Request-Id", "")
        request_id = incoming_id if _REQUEST_ID.fullmatch(incoming_id) else uuid4().hex
        rejection = size_rejection or (hardening_layer.reject(request) if hardening_layer else None)
        preflight = hardening_layer.preflight(request) if hardening_layer else None
        if rejection is not None:
            response = rejection
        elif preflight is not None:
            response = preflight
        else:
            response = _dispatch(request, route)
        duration_ms = (perf_counter() - started_at) * 1000
        extra_headers: tuple = ()
        if hardening_layer:
            response = hardening_layer.finalize(request, response, request_id=request_id, duration_ms=duration_ms)
        elif not any(name.lower() == "x-request-id" for name, _ in response.headers):
            extra_headers = (("X-Request-ID", request_id),)
        payload = response.body if isinstance(response.body, bytes) else response.body.encode("utf-8")
        start_response(
            f"{response.status} {_STATUS_TEXT.get(response.status, 'OK')}",
            [("Content-Type", response.content_type), ("Content-Length", str(len(payload))), *response.headers, *extra_headers],
        )
        return [payload]

    return application


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    print("Native-router benchmark app (dev server): http://127.0.0.1:9008/")
    make_server("127.0.0.1", 9008, build_application()).serve_forever()
