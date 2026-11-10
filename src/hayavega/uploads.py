"""Local-disk storage for uploaded files -- a plain helper, not an
abstracted storage-backend system (S3 and similar remain out of scope).
"""

from __future__ import annotations

from pathlib import Path

from .request import UploadedFile


def save_uploaded_file(file: UploadedFile, directory: str | Path) -> Path:
    """Write `file`'s content to `directory` and return the resulting path.

    Only the basename of `file.filename` is used -- directory-traversal
    segments (e.g. ``../../etc/passwd``) are stripped -- but the name
    itself is still client-supplied. A caller that needs a safe, unique,
    or collision-free name (most production uses) should generate one
    itself rather than trust `file.filename` directly; this is a plain
    local-dev convenience, not a hardened upload pipeline.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name or "upload"
    destination = directory / safe_name
    destination.write_bytes(file.content)
    return destination
