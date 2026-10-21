"""Temporary Python development runtime; replace the dispatcher with Rust incrementally."""

import html
import logging
import re
import warnings
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4
from wsgiref.simple_server import make_server

from . import db
from .hardening import Hardening, HardeningOptions
from .middleware import build_chain
from .request import Request
from .response import Response, json_response
from .swagger import documentation_routes

if TYPE_CHECKING:
    from .addons.signals import SignalBus
    from .metrics import MetricsRegistry

LOGGER = logging.getLogger("mangoo.runtime")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STATUS_TEXT = {
    200: "OK",
    204: "No Content",
    301: "Moved Permanently",
    304: "Not Modified",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    429: "Too Many Requests",
    500: "Internal Server Error",
}


def _available_routes_html(route_list) -> str:
    """List registered route patterns for a debug-mode 404 page.

    Computed once at application-build time, not per-request -- a 404's
    actionable next step ("here's what *is* registered, check urlpatterns
    for the one you meant") only costs anything when ``debug=True``, never
    in production. Never enabled unless the application opted into
    ``debug=True`` -- listing an app's own route patterns is standard
    development-mode behavior (the same tradeoff Django's DEBUG 404 page
    makes), not the kind of confidential system detail (credentials, host
    info) `_error_response` above is careful to never expose regardless
    of `debug`.
    """
    items = "".join(f"<li>/{html.escape(route.pattern)}</li>" for route in sorted(route_list, key=lambda route: route.pattern))
    return f"<p>Registered routes:</p><ul>{items}</ul>" if items else ""


def _error_response(route, exc: Exception, *, debug: bool) -> Response:
    """Build a safe 500 response for an unhandled view exception.

    Never includes the exception's message or any traceback detail --
    only the exception's *type name* (e.g. ``"ValueError"``), which
    carries no confidential content by itself. A connection error's
    message routinely embeds the database host, port, and username it
    tried; a settings-validation error can embed a secret's name; neither
    should ever reach a client regardless of ``debug``. The full
    exception, with traceback, is always logged server-side by the caller.
    API routes (``api_route``) get the documented ``{"detail": string}``
    JSON envelope with an added ``error_type``; ordinary page routes get
    a small HTML page. ``debug`` only controls whether a developer hint to
    check the server log is added -- it never changes what's exposed.
    """
    error_type = type(exc).__name__
    if route is not None and route.is_api:
        detail = {"detail": "Internal Server Error", "error_type": error_type}
        return json_response(detail, 500)
    hint = "<p>See the server log for details.</p>" if debug else ""
    return Response(f"<h1>500 Internal Server Error</h1><p>Error type: {error_type}</p>{hint}", status=500)


def create_application(
    urlpatterns,
    *,
    hardening: HardeningOptions | None = None,
    debug: bool = False,
    signals: "SignalBus | None" = None,
    pulse: "SignalBus | None" = None,
    metrics: "MetricsRegistry | None" = None,
    api_title: str = "Mangoo API",
    api_version: str = "0.1.0",
    pool_connections: bool = False,
    middleware: list | None = None,
):
    """Build the WSGI application for a route list.

    ``pool_connections=True`` calls ``mangoo.db.enable_connection_pooling()``
    -- a convenience, not a scoped setting: pooling is process-global (one
    connection reused per worker thread per database config), so it affects
    every ``mangoo.db.connect()`` call in the process, not only requests
    served by this particular application object. See
    ``enable_connection_pooling``'s docstring for what this does and does
    not solve before enabling it against MySQL/PostgreSQL specifically.

    This is separated from ``serve`` so application behavior can be tested
    without opening a network socket. ``signals``, when given a ``SignalBus``,
    publishes ``request.started``/``request.finished`` asynchronously so a
    slow subscriber cannot add latency to the response. ``pulse`` is a
    deprecated alias for ``signals``. ``metrics``, when
    given a ``MetricsRegistry``, records one request-count and duration
    observation per completed request, labeled by the matched route pattern;
    pair it with ``mangoo.metrics.metrics_routes`` to expose ``/metrics``.
    ``api_title`` and ``api_version`` name the generated OpenAPI document at
    ``/openapi.json``; set ``api_version`` to version your API independently
    of the package. ``middleware``, when given a list of already-resolved
    middleware factories (``mangoo.middleware.load_middleware(Settings.middleware)``),
    wraps route matching and the view call -- it runs after hardening
    (which always runs first and can't be bypassed by a middleware) and
    before the matched view. See ``mangoo.middleware`` for the factory
    contract.
    """
    if pulse is not None:
        warnings.warn("`pulse=` is deprecated; use `signals=` instead.", DeprecationWarning, stacklevel=2)
        signals = pulse if signals is None else signals
    if pool_connections:
        db.enable_connection_pooling()
    route_list = [*urlpatterns]
    route_list.extend(documentation_routes(route_list, title=api_title, version=api_version))
    routes = {route.pattern: route for route in route_list}
    wildcard_routes = [route for route in route_list if route.pattern.endswith("/<path>")]
    hardening_layer = Hardening(hardening) if hardening else None
    available_routes_html = _available_routes_html(route_list) if debug else ""

    def _match(requested: str):
        route = routes.get(requested)
        if route is None:
            route = next((item for item in wildcard_routes if requested.startswith(item.pattern.removesuffix("/<path>") + "/")), None)
        return route

    def _dispatch(request) -> Response:
        """Route matching, the view call, and error handling -- the one
        part of the pipeline a middleware chain wraps. Hardening's
        reject/preflight checks happen in the caller, before this is
        reached, and can't be bypassed by a middleware here."""
        requested = request.path.strip("/")
        route = _match(requested)
        if route is None:
            body = f"<h1>404 Not Found</h1><p>No route matches '/{html.escape(requested)}'.</p>{available_routes_html}" if debug else "<h1>404 Not Found</h1>"
            return Response(body, status=404)
        if request.method not in route.methods:
            return Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", ", ".join(route.methods)),))
        try:
            result = route.view(request)
            return result if isinstance(result, Response) else Response(str(result))
        except Exception as exc:
            LOGGER.exception("Unhandled request error path=%s", request.path)
            return _error_response(route, exc, debug=debug)

    dispatch = build_chain(middleware or (), _dispatch)

    def application(environ, start_response):
        started_at = perf_counter()
        requested = environ.get("PATH_INFO", "/").strip("/")
        route = _match(requested)
        try:
            content_length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            content_length = 0
        size_rejection = hardening_layer.reject_content_length(content_length) if hardening_layer else None
        request = Request.from_wsgi(environ, read_body=size_rejection is None)
        incoming_id = request.headers.get("X-Request-Id", "")
        request_id = incoming_id if _REQUEST_ID.fullmatch(incoming_id) else uuid4().hex
        if signals is not None:
            signals.publish("request.started", data={"method": request.method, "path": request.path})
        rejection = size_rejection or (hardening_layer.reject(request) if hardening_layer else None)
        preflight = hardening_layer.preflight(request) if hardening_layer else None
        if rejection is not None:
            response = rejection
        elif preflight is not None:
            response = preflight
        else:
            response = dispatch(request)
        duration_ms = (perf_counter() - started_at) * 1000
        if signals is not None:
            signals.publish("request.finished", data={"method": request.method, "path": request.path, "status": response.status, "duration_ms": round(duration_ms, 2)})
        extra_headers: tuple[tuple[str, str], ...] = ()
        if hardening_layer:
            response = hardening_layer.finalize(request, response, request_id=request_id, duration_ms=duration_ms)
        elif not any(name.lower() == "x-request-id" for name, _ in response.headers):
            extra_headers = (("X-Request-ID", request_id),)
        if metrics is not None:
            metrics.record(method=request.method, path=route.pattern if route is not None else "unmatched", status=response.status, duration_ms=duration_ms)
        payload = response.body if isinstance(response.body, bytes) else response.body.encode("utf-8")
        start_response(
            f"{response.status} {_STATUS_TEXT.get(response.status, 'OK')}",
            [("Content-Type", response.content_type), ("Content-Length", str(len(payload))), *response.headers, *extra_headers],
        )
        return [payload]

    return application


def serve(urlpatterns, host: str = "127.0.0.1", port: int = 8000, *, hardening: HardeningOptions | None = None, debug: bool = False) -> None:
    application = create_application(urlpatterns, hardening=hardening, debug=debug)

    print(f"Mangoo development server: http://{host}:{port}/")
    make_server(host, port, application).serve_forever()
