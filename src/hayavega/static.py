"""Safe static-file routes for development and small deployments."""

from __future__ import annotations

import mimetypes
import stat
from hashlib import sha256
from pathlib import Path
from threading import Lock

from .response import Response
from .routing import path

# mimetypes.guess_type() is OS-dependent for these extensions: it also reads
# OS-provided mime databases (the Windows Registry, /etc/mime.types on
# Linux), which can carry different or stale values across machines --
# .js in particular can resolve to the obsolete "application/javascript"
# or the current RFC 9239 standard "text/javascript" depending entirely on
# what happens to be registered on that specific host, not on Hayavega or
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
    # `Path.resolve()` is a real filesystem syscall (profiled to dominate this
    # route's request time) -- cache the *resolved, in-bounds* path per
    # `relative` so a repeat request for the same file skips it. Only a
    # successful resolution is ever cached: an attacker sending arbitrary,
    # never-repeating paths can't grow this dict, since a rejected path is
    # never stored.
    #
    # Existence and content are still checked fresh on every request via a
    # single `stat()` call (merging what used to be a separate `is_file()`
    # check plus the mtime this cache needs -- the same "one syscall, not
    # two" fix templates.py's own `_find_template_file` already made, found
    # via profiling). If the file's mtime hasn't changed since it was last
    # read, the cached content/ETag/content-type are reused instead of
    # re-reading and re-hashing the file; a modified file (new mtime) or a
    # removed one (`stat()` raises) is never served stale.
    _resolved: dict[str, Path] = {}
    _content_cache: dict[Path, tuple[float, bytes | str, str, str]] = {}
    _cache_lock = Lock()

    def serve(request):
        relative = request.path.strip("/").removeprefix(prefix).lstrip("/")
        target = _resolved.get(relative)
        if target is None:
            candidate = (root / relative).resolve()
            if root not in candidate.parents:
                return Response("<h1>404 Not Found</h1>", status=404)
            target = _resolved[relative] = candidate
        try:
            result = target.stat()
        except OSError:
            return Response("<h1>404 Not Found</h1>", status=404)
        if not stat.S_ISREG(result.st_mode):
            return Response("<h1>404 Not Found</h1>", status=404)
        mtime = result.st_mtime
        with _cache_lock:
            cached = _content_cache.get(target)
        if cached is not None and cached[0] == mtime:
            _, body, etag, content_type = cached
        else:
            content_type = _CONTENT_TYPE_OVERRIDES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            raw = target.read_bytes()
            etag = '"' + sha256(raw).hexdigest() + '"'
            body = raw.decode("utf-8") if content_type.startswith("text/") or content_type == "image/svg+xml" else raw
            with _cache_lock:
                _content_cache[target] = (mtime, body, etag, content_type)
        headers = (("Cache-Control", f"public, max-age={max_age}"), ("ETag", etag), ("X-Content-Type-Options", "nosniff"))
        if getattr(request, "headers", {}).get("If-None-Match") == etag:
            return Response(b"", status=304, content_type=content_type, headers=headers)
        return Response(body, content_type=content_type, headers=headers)

    return [path(f"{prefix}/<path>", serve)]
