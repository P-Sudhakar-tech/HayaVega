"""Safe static-file routes for development and small deployments."""

from __future__ import annotations

import mimetypes
from hashlib import sha256
from pathlib import Path

from .response import Response
from .routing import path

# mimetypes.guess_type() is OS-dependent for these extensions: it also reads
# OS-provided mime databases (the Windows Registry, /etc/mime.types on
# Linux), which can carry different or stale values across machines --
# .js in particular can resolve to the obsolete "application/javascript"
# or the current RFC 9239 standard "text/javascript" depending entirely on
# what happens to be registered on that specific host, not on Mangoo or
# Python's own version. Overridden here with fixed, standards-current
# values so a static asset's content type is identical on every platform.
_CONTENT_TYPE_OVERRIDES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}


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
        content_type = _CONTENT_TYPE_OVERRIDES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        content = target.read_bytes()
        etag = '"' + sha256(content).hexdigest() + '"'
        headers = (("Cache-Control", f"public, max-age={max_age}"), ("ETag", etag), ("X-Content-Type-Options", "nosniff"))
        if getattr(request, "headers", {}).get("If-None-Match") == etag:
            return Response(b"", status=304, content_type=content_type, headers=headers)
        body = content.decode("utf-8") if content_type.startswith("text/") or content_type == "image/svg+xml" else content
        return Response(body, content_type=content_type, headers=headers)

    return [path(f"{prefix}/<path>", serve)]
