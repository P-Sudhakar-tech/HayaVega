from dataclasses import dataclass
from collections.abc import Callable


@dataclass(frozen=True)
class Route:
    pattern: str
    view: Callable
    methods: tuple[str, ...] = ("GET",)


def path(pattern: str, view: Callable, methods: tuple[str, ...] = ("GET",)) -> Route:
    """Register a route. The root route is written as an empty string."""
    return Route(pattern.strip("/"), view, tuple(method.upper() for method in methods))
