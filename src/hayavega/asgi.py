"""ASGI 3.0 application support -- additive to the WSGI runtime in ``runtime.py``.

Deploy under an ASGI server: ``uvicorn config.asgi:application`` or
``hypercorn config.asgi:application``. This is a second transport for the
*same* ``Route``/``Request``/``Response``/``Hardening``/Signals/Metrics
objects the WSGI path already uses -- nothing here changes
``create_application`` or the WSGI-only ``wsgiref``/gunicorn/waitress
deployment story documented in ``DEPLOYMENT.md``, which stays the default.
Choose ASGI only when a project needs ``async def`` views for concurrent
I/O (calling another service, awaiting a database driver's async API) that
WSGI's one-thread-per-request model can't overlap as cheaply.

Views may be ``async def`` (awaited directly on the event loop) or plain
synchronous functions (run via a dedicated worker-thread pool, so existing
sync views work completely unmodified under either transport).
"""

from __future__ import annotations

import asyncio
import html
import inspect
import logging
import re
import warnings
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import TYPE_CHECKING
from urllib.parse import parse_qs
from uuid import uuid4

from . import db
from .hardening import Hardening, HardeningOptions
from .middleware import build_chain
from .request import Request
from .response import Response, json_response
from .swagger import documentation_routes
from .websocket import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from .addons.signals import SignalBus
    from .metrics import MetricsRegistry
    from .websocket import WebSocketRoute

LOGGER = logging.getLogger("hayavega.asgi")
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


def _scope_header(scope: dict, name: bytes) -> str:
    for raw_name, raw_value in scope.get("headers", ()):
        if raw_name == name:
            return raw_value.decode("latin-1")
    return ""


def _reject_websocket(scope: dict, hardening_layer: Hardening | None) -> int | None:
    """Return a close code if this connection should be rejected before accept, else None.

    WebSocket connections bypass the browser's same-origin/CORS enforcement
    that protects ordinary HTTP requests -- a page on any origin can open a
    WebSocket to any host and have the browser send cookies along with it
    ("cross-site WebSocket hijacking"). Reusing ``HardeningOptions.allowed_hosts``/
    ``allowed_origins`` here (the same settings ``Hardening.reject()`` already
    enforces for HTTP) closes that gap for WebSocket routes the same way, if
    either is configured; deployments that already trust every origin can
    leave them unset and every connection is accepted, matching this
    framework's existing "opt-in, not on by default" hardening posture.
    """
    if hardening_layer is None:
        return None
    options = hardening_layer.options
    if options.allowed_hosts and "*" not in options.allowed_hosts:
        host = _scope_header(scope, b"host").split(":", 1)[0].lower()
        if host not in options.allowed_hosts:
            return 4403
    if options.allowed_origins:
        origin = _scope_header(scope, b"origin")
        if origin and origin not in options.allowed_origins:
            return 4403
    return None


async def _handle_websocket(scope: dict, receive, send, *, route: WebSocketRoute | None, hardening_layer: Hardening | None, signals: SignalBus | None) -> None:
    connect_message = await receive()
    if connect_message["type"] != "websocket.connect":
        return
    rejection_code = _reject_websocket(scope, hardening_layer)
    path = scope.get("path", "/")
    if rejection_code is not None:
        if signals is not None:
            signals.publish("websocket.rejected", data={"path": path, "code": rejection_code})
        await send({"type": "websocket.close", "code": rejection_code})
        return
    if route is None:
        await send({"type": "websocket.close", "code": 1000})
        return
    ws = WebSocket(scope, receive, send)
    if signals is not None:
        signals.publish("websocket.connected", data={"path": path})
    try:
        await route.handler(ws)
    except WebSocketDisconnect:
        pass
    except Exception:
        LOGGER.exception("Unhandled websocket handler error path=%s", path)
        if not ws.closed:
            try:
                await ws.close(code=1011)
            except Exception:
                pass  # the connection may already be gone; nothing more to do
    finally:
        # A handler that returns (normally, or by not calling accept() at
        # all to silently reject) without itself sending websocket.close
        # would otherwise leave the client waiting forever -- this is the
        # one place that's guaranteed to run regardless of how the handler
        # exited, so it's where that gets closed out.
        if not ws.closed:
            try:
                await ws.close(code=1000)
            except Exception:
                pass  # the connection may already be gone; nothing more to do
        if signals is not None:
            signals.publish("websocket.disconnected", data={"path": path})


async def _handle_lifespan(receive, send) -> None:
    """Answer ASGI lifespan events so servers like uvicorn don't hang waiting for a reply.

    Hayavega has no startup/shutdown hooks of its own yet, so every lifespan
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
    api_title: str = "Hayavega API",
    api_version: str = "0.1.0",
    pool_connections: bool = False,
    max_sync_workers: int = 16,
    websocket_patterns: list[WebSocketRoute] | None = None,
    middleware: list | None = None,
):
    """Build the ASGI application for a route list.

    Accepts the same ``hardening``/``debug``/``signals``/``metrics``/
    ``api_title``/``api_version``/``pool_connections`` arguments as
    ``runtime.create_application``, with identical behavior (request
    hardening, Signals events, metrics recording, OpenAPI/Swagger routes)
    -- only the transport differs. Pooling is thread-local and sync views
    run via a dedicated worker-thread pool (see ``max_sync_workers`` below),
    so each of those threads gets its own reused connection across the
    requests it handles, the same benefit WSGI's thread-per-request model
    gets. ``pulse`` is a deprecated alias for ``signals``, kept for parity
    with the WSGI path.

    ``max_sync_workers`` (default 16, matching this project's own documented
    ``waitress --threads=16`` guidance in ``DEPLOYMENT.md``) sizes a
    dedicated ``ThreadPoolExecutor`` that every synchronous view runs on,
    instead of ``asyncio.to_thread``'s event-loop-wide default executor.
    That default executor is sized ``min(32, os.cpu_count() + 4)`` --
    tuned for occasional CPU-bound offloading, not for a whole
    application's worth of concurrent I/O-bound sync views, and on a
    modest machine (4 cores -> 8 workers) it becomes a real queuing
    bottleneck under realistic concurrency well before the database or the
    network does. An in-process microbenchmark for this
    (``benchmarks/asgi_executor_sizing.py``) was tried first but proved too
    noisy on this environment to trust on its own -- repeated runs
    disagreed on which worker count won. The real evidence is the
    HTTP-level before/after comparison in
    ``benchmarks/README.md#asgi-vs-fastapi``: a real ``uvicorn`` server
    under real ``oha`` load, run twice before and twice after this change,
    which was large and consistent in every run (e.g. ``json_echo`` roughly
    doubled). Benchmark your own deployment's concurrency and core count
    before assuming 16 is right for it, the same standing advice
    ``enable_connection_pooling()`` and ``DEPLOYMENT.md`` already give for
    their own tunables.

    Pass ``websocket_patterns=[websocket_path(...), ...]`` (from
    ``hayavega.websocket``) to add WebSocket routes; see that module for the
    handler contract. WebSocket connections have no WSGI equivalent, so this
    parameter only ever does anything under this ASGI application. Reuses
    ``hardening``'s ``allowed_hosts``/``allowed_origins`` to reject
    cross-origin WebSocket connections the same way HTTP requests already
    are, if either is configured.

    ``middleware``, when given a list of already-resolved middleware
    factories (``hayavega.middleware.load_middleware(Settings.middleware)``),
    wraps route matching and the view call for HTTP requests the same way
    ``runtime.create_application`` does -- after hardening, before the
    matched view. A middleware's returned handler must be ``async def``
    here (check ``inspect.iscoroutinefunction`` if it needs to support
    both transports); it does not apply to WebSocket connections.
    """
    if pulse is not None:
        warnings.warn("`pulse=` is deprecated; use `signals=` instead.", DeprecationWarning, stacklevel=2)
        signals = pulse if signals is None else signals
    if pool_connections:
        db.enable_connection_pooling()
    sync_executor = ThreadPoolExecutor(max_workers=max_sync_workers, thread_name_prefix="hayavega-asgi-sync")
    route_list = [*urlpatterns]
    route_list.extend(documentation_routes(route_list, title=api_title, version=api_version))
    routes = {route.pattern: route for route in route_list}
    wildcard_routes = [route for route in route_list if route.pattern.endswith("/<path>")]
    websocket_routes = {route.pattern: route for route in (websocket_patterns or ())}
    hardening_layer = Hardening(hardening) if hardening else None
    available_routes_html = _available_routes_html(route_list) if debug else ""

    def _match(requested: str):
        route = routes.get(requested)
        if route is None:
            route = next((item for item in wildcard_routes if requested.startswith(item.pattern.removesuffix("/<path>") + "/")), None)
        return route

    async def _dispatch(request) -> Response:
        """Route matching, the view call, and error handling -- the one
        part of the pipeline a middleware chain wraps. Hardening's
        reject/preflight checks happen in the caller, before this is
        reached, and can't be bypassed by a middleware here."""
        requested = request.path.strip("/")
        route = _match(requested)
        if route is None:
            not_found_body = f"<h1>404 Not Found</h1><p>No route matches '/{html.escape(requested)}'.</p>{available_routes_html}" if debug else "<h1>404 Not Found</h1>"
            return Response(not_found_body, status=404)
        if request.method not in route.methods:
            return Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", ", ".join(route.methods)),))
        try:
            if inspect.iscoroutinefunction(route.view):
                result = await route.view(request)
            else:
                result = await asyncio.get_running_loop().run_in_executor(sync_executor, route.view, request)
            return result if isinstance(result, Response) else Response(str(result))
        except Exception as exc:
            LOGGER.exception("Unhandled request error path=%s", request.path)
            return _error_response(route, exc, debug=debug)

    dispatch = build_chain(middleware or (), _dispatch)

    async def application(scope: dict, receive, send) -> None:
        if scope["type"] == "lifespan":
            await _handle_lifespan(receive, send)
            sync_executor.shutdown(wait=False)
            return
        if scope["type"] == "websocket":
            websocket_route = websocket_routes.get(scope.get("path", "/").strip("/"))
            await _handle_websocket(scope, receive, send, route=websocket_route, hardening_layer=hardening_layer, signals=signals)
            return
        if scope["type"] != "http":
            raise ValueError(f"Hayavega's ASGI application only supports 'http', 'websocket', and 'lifespan' scopes, got {scope['type']!r}.")

        started_at = perf_counter()
        requested = scope.get("path", "/").strip("/")
        route = _match(requested)

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
        else:
            response = await dispatch(request)
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
