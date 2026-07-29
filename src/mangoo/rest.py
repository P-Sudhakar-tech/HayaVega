"""Small REST API layer built on Mangoo routes and JSON responses."""
from collections.abc import Callable, Iterable

from .response import Response, json_response
from .routing import Route, path


def api_route(pattern: str, view: Callable, methods: Iterable[str] = ("GET",)) -> Route:
    """Create a JSON endpoint. Views may return a dict/list or a Response."""
    def endpoint(request):
        result = view(request)
        return result if isinstance(result, Response) else json_response(result)

    route = path(pattern, endpoint, tuple(methods))
    return Route(route.pattern, route.view, route.methods, is_api=True)
