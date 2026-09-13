from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    pattern: str
    view: Callable
    methods: tuple[str, ...] = ("GET",)
    is_api: bool = False
    secured: bool = False
    request_schema: dict | None = None
    responses: dict | None = None


def path(pattern: str, view: Callable, methods: tuple[str, ...] = ("GET",)) -> Route:
    """Register a route. The root route is written as an empty string."""
    return Route(pattern.strip("/"), view, tuple(method.upper() for method in methods))
