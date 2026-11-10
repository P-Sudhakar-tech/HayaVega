"""Request/response middleware, composed from a project's own `Settings.middleware` list.

A middleware is a factory: ``middleware(get_response) -> handler(request) -> Response``.
Hayavega composes a `Settings.middleware` list the same way Django's modern
middleware does -- the first entry is the *outermost* wrapper (sees the
request first, the response last); the last entry is innermost (closest
to the view). Sits between hardening (which always runs first and can't
be bypassed by a middleware, by design) and the matched view.

A middleware factory's returned handler must match ``get_response``'s
sync/async-ness -- the same contract `security.csrf_protect`/
`permission_required` already use for views (check
``inspect.iscoroutinefunction(get_response)`` if a middleware needs to
support both transports).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable


def load_middleware(dotted_paths: Iterable[str]) -> list[Callable]:
    """Resolve each dotted path (e.g. ``"myapp.middleware.log_requests"``) to its factory callable.

    The same dotted-path-from-settings convention ``hayavega.jobs``'
    ``job_handlers`` and the management command registry already use --
    one resolution mechanism, not several.
    """
    factories = []
    for dotted_path in dotted_paths:
        module_path, _, attribute = dotted_path.rpartition(".")
        if not module_path:
            raise ValueError(f"Not a dotted path: {dotted_path!r}")
        module = __import__(module_path, fromlist=[attribute])
        factories.append(getattr(module, attribute))
    return factories


def build_chain(factories: Iterable[Callable], handler: Callable) -> Callable:
    """Wrap ``handler`` with each factory in turn, first-listed ending up outermost."""
    for factory in reversed(list(factories)):
        handler = factory(handler)
    return handler
