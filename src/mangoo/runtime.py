"""Temporary Python development runtime; replace the dispatcher with Rust incrementally."""
from wsgiref.simple_server import make_server

from .request import Request
from .response import Response


def create_application(urlpatterns):
    """Build the WSGI application for a route list.

    This is separated from ``serve`` so application behavior can be tested
    without opening a network socket.
    """
    routes = {route.pattern: route for route in urlpatterns}

    def application(environ, start_response):
        requested = environ.get("PATH_INFO", "/").strip("/")
        route = routes.get(requested)
        request = Request.from_wsgi(environ)
        if route is None:
            response = Response("<h1>404 Not Found</h1>", status=404)
        elif request.method not in route.methods:
            response = Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", ", ".join(route.methods)),))
        else:
            result = route.view(request)
            response = result if isinstance(result, Response) else Response(str(result))
        status_text = {200: "OK", 204: "No Content", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed"}.get(response.status, "OK")
        payload = response.body.encode("utf-8")
        start_response(
            f"{response.status} {status_text}",
            [("Content-Type", response.content_type), ("Content-Length", str(len(payload))), *response.headers],
        )
        return [payload]

    return application


def serve(urlpatterns, host: str = "127.0.0.1", port: int = 8000) -> None:
    application = create_application(urlpatterns)

    print(f"Mangoo development server: http://{host}:{port}/")
    make_server(host, port, application).serve_forever()
