"""Static-asset route for Hayavega's built-in hypermedia runtime (hayavega-ui.js / hayavega-ui.css)."""

from __future__ import annotations

from pathlib import Path

from ..static import static_routes

_STATIC_DIR = Path(__file__).parent / "static"


def ui_routes(prefix: str = "hayavega-ui", *, max_age: int = 86400):
    """Serve the bundled ``hayavega-ui.js`` and ``hayavega-ui.css``.

    Mount once: ``*ui_routes()`` adds them at ``/hayavega-ui/hayavega-ui.js`` and
    ``/hayavega-ui/hayavega-ui.css`` (with the default ``prefix``), then
    reference those paths from ``Page(..., scripts=[...], stylesheets=[...])``.
    """
    return static_routes(_STATIC_DIR, prefix, max_age=max_age)
