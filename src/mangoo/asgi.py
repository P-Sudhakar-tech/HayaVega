"""ASGI 3.0 application support -- additive to the WSGI runtime in ``runtime.py``.

Deploy under an ASGI server: ``uvicorn project.asgi:application`` or
``hypercorn project.asgi:application``. This is a second transport for the
*same* ``Route``/``Request``/``Response``/``Hardening``/Signals/Metrics
objects the WSGI path already uses -- nothing here changes
``create_application`` or the WSGI-only ``wsgiref``/gunicorn/waitress
deployment story documented in ``DEPLOYMENT.md``, which stays the default.
Choose ASGI only when a project needs ``async def`` views for concurrent
I/O (calling another service, awaiting a database driver's async API) that
WSGI's one-thread-per-request model can't overlap as cheaply.

Views may be ``async def`` (awaited directly on the event loop) or plain
synchronous functions (run via ``asyncio.to_thread``, so existing sync views
work completely unmodified under either transport).
"""

from __future__ import annotations

import asyncio
import html
import inspect
import logging
import re
import warnings
from time import perf_counter
from typing import TYPE_CHECKING
from urllib.parse import parse_qs
from uuid import uuid4

from .hardening import Hardening, HardeningOptions
from .request import Request
from .response import Response, json_response
from .swagger import documentation_routes

if TYPE_CHECKING:
    from .addons.signals import SignalBus
    from .metrics import MetricsRegistry

LOGGER = logging.getLogger("mangoo.asgi")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


async def _read_body(receive) -> bytes:
    chunks = []
    more_body = True
    while more_body:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        more_body = message.get("more_body", False)
    return b"".join(chunks)


def _declared_content_length(scope: dict) -> int:
    for raw_name, raw_value in scope.get("headers", ()):
        if raw_name == b"content-length":
            try:
                return int(raw_value)
            except ValueError:
                return 0
    return 0


def _request_from_scope(scope: dict, body: bytes) -> Request:
    headers = {raw_name.decode("latin-1").title(): raw_value.decode("latin-1") for raw_name, raw_value in scope.get("headers", ())}
    query_string = scope.get("query_string", b"").decode("latin-1")
    client = scope.get("client") or ("", 0)
    return Request(
        method=scope.get("method", "GET").upper(),
        path=scope.get("path", "/"),
        query_params=parse_qs(query_string),
        body=body,
        headers=headers,
        remote_addr=client[0],
        is_secure=scope.get("scheme", "http") == "https",
        raw_query=query_string,
    )


async def _handle_lifespan(receive, send) -> None:
    """Answer ASGI lifespan events so servers like uvicorn don't hang waiting for a reply.

    Mangoo has no startup/shutdown hooks of its own yet, so every lifespan
    message is acknowledged immediately.
    """
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def create_asgi_application(
    urlpatterns,
    *,
    hardening: HardeningOptions | None = None,
    debug: bool = False,
    signals: SignalBus | None = None,
    pulse: SignalBus | None = None,
    metrics: MetricsRegistry | None = None,
    api_title: str = "Mangoo API",
    api_version: str = "0.1.0",
):
    """Build the ASGI application for a route list.

    Accepts the same ``hardening``/``debug``/``signals``/``metrics``/
    ``api_title``/``api_version`` arguments as ``runtime.create_application``,
    with identical behavior (request hardening, Signals events, metrics
    recording, OpenAPI/Swagger routes) -- only the transport differs. ``pulse``
    is a deprecated alias for ``signals``, kept for parity with the WSGI path.
    """
    if pulse is not None:
        warnings.warn("`pulse=` is deprecated; use `signals=` instead.", DeprecationWarning, stacklevel=2)
        signals = pulse if signals is None else signals
    route_list = [*urlpatterns]
    route_list.extend(documentation_routes(route_list, title=api_title, version=api_version))
    routes = {route.pattern: route for route in route_list}
    wildcard_routes = [route for route in route_list if route.pattern.endswith("/<path>")]
    hardening_layer = Hardening(hardening) if hardening else None
    available_routes_html = _available_routes_html(route_list) if debug else ""

    async def application(scope: dict, receive, send) -> None:
        if scope["type"] == "lifespan":
            await _handle_lifespan(receive, send)
            return
        if scope["type"] != "http":
            raise ValueError(f"Mangoo's ASGI application only supports 'http' and 'lifespan' scopes, got {scope['type']!r}.")

        started_at = perf_counter()
        requested = scope.get("path", "/").strip("/")
        route = routes.get(requested)
        if route is None:
            route = next((item for item in wildcard_routes if requested.startswith(item.pattern.removesuffix("/<path>") + "/")), None)

        size_rejection = hardening_layer.reject_content_length(_declared_content_length(scope)) if hardening_layer else None
        body = b"" if size_rejection is not None else await _read_body(receive)
        request = _request_from_scope(scope, body)
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
        elif route is None:
            not_found_body = f"<h1>404 Not Found</h1><p>No route matches '/{html.escape(requested)}'.</p>{available_routes_html}" if debug else "<h1>404 Not Found</h1>"
            response = Response(not_found_body, status=404)
        elif request.method not in route.methods:
            response = Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", ", ".join(route.methods)),))
        else:
            try:
                if inspect.iscoroutinefunction(route.view):
                    result = await route.view(request)
                else:
                    result = await asyncio.to_thread(route.view, request)
                response = result if isinstance(result, Response) else Response(str(result))
            except Exception as exc:
                LOGGER.exception("Unhandled request error path=%s", request.path)
                response = _error_response(route, exc, debug=debug)
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
        # ASGI requires header names to be lowercase bytes (unlike WSGI, which
        # leaves casing to the application); Response objects are built with
        # human-readable casing shared by both transports, so it is lowercased
        # only here, at the ASGI boundary.
        response_headers: list[tuple[bytes, bytes]] = [
            (b"content-type", response.content_type.encode("latin-1")),
            (b"content-length", str(len(payload)).encode("latin-1")),
        ]
        response_headers.extend((name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in (*response.headers, *extra_headers))
        await send({"type": "http.response.start", "status": response.status, "headers": response_headers})
        await send({"type": "http.response.body", "body": payload})

    return application
