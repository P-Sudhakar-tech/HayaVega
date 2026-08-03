"""Safe static-file routes for development and small deployments."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from hashlib import sha256

from .response import Response
from .routing import path


def static_routes(directory: str | Path, prefix: str = "static", *, max_age: int = 3600):
    """Serve development assets with safe MIME types and cache validators."""
    if max_age < 0:
        raise ValueError("max_age cannot be negative")
    root = Path(directory).resolve()
    prefix = prefix.strip("/")
    def serve(request):
        relative = request.path.strip("/").removeprefix(prefix).lstrip("/")
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            return Response("<h1>404 Not Found</h1>", status=404)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        content = target.read_bytes()
        etag = '"' + sha256(content).hexdigest() + '"'
        headers = (("Cache-Control", f"public, max-age={max_age}"), ("ETag", etag), ("X-Content-Type-Options", "nosniff"))
        if getattr(request, "headers", {}).get("If-None-Match") == etag:
            return Response(b"", status=304, content_type=content_type, headers=headers)
        body = content.decode("utf-8") if content_type.startswith("text/") or content_type == "image/svg+xml" else content
        return Response(body, content_type=content_type, headers=headers)
    return [path(f"{prefix}/<path>", serve)]
