"""Safe static-file routes for development and small deployments."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from .response import Response
from .routing import path


def static_routes(directory: str | Path, prefix: str = "static"):
    root = Path(directory).resolve()
    prefix = prefix.strip("/")
    def serve(request):
        relative = request.path.strip("/").removeprefix(prefix).lstrip("/")
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            return Response("<h1>404 Not Found</h1>", status=404)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return Response(target.read_text(encoding="utf-8"), content_type=content_type) if content_type.startswith("text/") or content_type == "image/svg+xml" else Response("<h1>415 Unsupported Media Type</h1>", status=415)
    return [path(f"{prefix}/<path>", serve)]
