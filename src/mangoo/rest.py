"""Small REST API layer built on Mangoo routes and JSON responses."""

import inspect
from collections.abc import Callable, Iterable

from .response import Response, json_response
from .routing import Route, path


def api_route(
    pattern: str,
    view: Callable,
    methods: Iterable[str] = ("GET",),
    *,
    secured: bool = False,
    request_schema: dict | None = None,
    responses: dict[int, dict] | None = None,
) -> Route:
    """Create a JSON endpoint. Views may return a dict/list or a Response.

    ``secured``, ``request_schema``, and ``responses`` describe the endpoint
    for the generated OpenAPI document at ``/openapi.json``. ``secured``
    marks the endpoint as requiring a Bearer session token. ``request_schema``
    is a JSON Schema dict for the request body. ``responses`` maps a status
    code to ``{"description": str, "schema": dict}`` (``schema`` is optional).
    None of these change runtime behavior -- omit them and Mangoo documents
    the endpoint with its previous generic fallback schema.

    ``view`` may be ``async def`` when deployed under ``mangoo.asgi`` (see
    ``DEPLOYMENT.md``): the wrapper stays a coroutine function itself so the
    ASGI runtime awaits it directly rather than running it in a thread.
    """

    if inspect.iscoroutinefunction(view):

        async def endpoint(request):
            result = await view(request)
            return result if isinstance(result, Response) else json_response(result)
    else:

        def endpoint(request):  # type: ignore[misc]
            result = view(request)
            return result if isinstance(result, Response) else json_response(result)

    route = path(pattern, endpoint, tuple(methods))
    return Route(route.pattern, route.view, route.methods, is_api=True, secured=secured, request_schema=request_schema, responses=responses)
