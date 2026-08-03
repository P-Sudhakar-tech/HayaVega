"""Temporary Python development runtime; replace the dispatcher with Rust incrementally."""
from wsgiref.simple_server import make_server

from .request import Request
from .response import Response
from .swagger import documentation_routes
from .hardening import Hardening, HardeningOptions
import logging
from time import perf_counter
import re
from uuid import uuid4

LOGGER = logging.getLogger("mangoo.runtime")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def create_application(urlpatterns, *, hardening: HardeningOptions | None = None, debug: bool = False):
    """Build the WSGI application for a route list.

    This is separated from ``serve`` so application behavior can be tested
    without opening a network socket.
    """
    route_list = [*urlpatterns]
    route_list.extend(documentation_routes(route_list))
    routes = {route.pattern: route for route in route_list}
    wildcard_routes = [route for route in route_list if route.pattern.endswith("/<path>")]
    hardening_layer = Hardening(hardening) if hardening else None

    def application(environ, start_response):
        started_at = perf_counter()
        requested = environ.get("PATH_INFO", "/").strip("/")
        route = routes.get(requested)
        if route is None:
            route = next((item for item in wildcard_routes if requested.startswith(item.pattern.removesuffix("/<path>") + "/")), None)
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
        elif route is None:
            response = Response("<h1>404 Not Found</h1>", status=404)
        elif request.method not in route.methods:
            response = Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", ", ".join(route.methods)),))
        else:
            try:
                result = route.view(request)
                response = result if isinstance(result, Response) else Response(str(result))
            except Exception:
                LOGGER.exception("Unhandled request error path=%s", request.path)
                response = Response("<h1>500 Internal Server Error</h1>" if not debug else "<h1>500 Internal Server Error</h1><pre>See server log for traceback.</pre>", status=500)
        duration_ms = (perf_counter() - started_at) * 1000
        if hardening_layer:
            response = hardening_layer.finalize(request, response, request_id=request_id, duration_ms=duration_ms)
        elif not any(name.lower() == "x-request-id" for name, _ in response.headers):
            response = Response(response.body, status=response.status, content_type=response.content_type, headers=(*response.headers, ("X-Request-ID", request_id)))
        status_text = {200: "OK", 204: "No Content", 301: "Moved Permanently", 304: "Not Modified", 308: "Permanent Redirect", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large", 415: "Unsupported Media Type", 429: "Too Many Requests", 500: "Internal Server Error"}.get(response.status, "OK")
        payload = response.body if isinstance(response.body, bytes) else response.body.encode("utf-8")
        start_response(
            f"{response.status} {status_text}",
            [("Content-Type", response.content_type), ("Content-Length", str(len(payload))), *response.headers],
        )
        return [payload]

    return application


def serve(urlpatterns, host: str = "127.0.0.1", port: int = 8000, *, hardening: HardeningOptions | None = None, debug: bool = False) -> None:
    application = create_application(urlpatterns, hardening=hardening, debug=debug)

    print(f"Mangoo development server: http://{host}:{port}/")
    make_server(host, port, application).serve_forever()
