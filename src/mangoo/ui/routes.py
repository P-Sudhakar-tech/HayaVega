"""Static-asset route for Mangoo's built-in hypermedia runtime (mangoo-ui.js / mangoo-ui.css)."""

from __future__ import annotations

from pathlib import Path

from ..static import static_routes

_STATIC_DIR = Path(__file__).parent / "static"


def ui_routes(prefix: str = "mangoo-ui", *, max_age: int = 86400):
    """Serve the bundled ``mangoo-ui.js`` and ``mangoo-ui.css``.

    Mount once: ``*ui_routes()`` adds them at ``/mangoo-ui/mangoo-ui.js`` and
    ``/mangoo-ui/mangoo-ui.css`` (with the default ``prefix``), then
    reference those paths from ``Page(..., scripts=[...], stylesheets=[...])``.
    """
    return static_routes(_STATIC_DIR, prefix, max_age=max_age)
