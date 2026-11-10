"""Local-disk storage for uploaded files -- a plain helper, not an
abstracted storage-backend system (S3 and similar remain out of scope).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .request import UploadedFile


def save_uploaded_file(file: UploadedFile, directory: str | Path, *, allowed_extensions: Iterable[str] | None = None) -> Path:
    """Write `file`'s content to `directory` and return the resulting path.

    Only the basename of `file.filename` is used -- directory-traversal
    segments (e.g. ``../../etc/passwd``) are stripped -- but the name
    itself is still client-supplied. A caller that needs a safe, unique,
    or collision-free name (most production uses) should generate one
    itself rather than trust `file.filename` directly; this is a plain
    local-dev convenience, not a hardened upload pipeline.

    `allowed_extensions` (e.g. ``(".png", ".jpg")``, case-insensitive) is
    opt-in and off by default, matching this framework's existing posture
    for other optional policy knobs (`auth.py`'s `validators=`,
    `hardening.py`'s `allowed_hosts=`). Without it, any extension is
    accepted, same as before -- a project that serves its uploads directory
    statically should pass this, since nothing stops an uploaded `.svg`/
    `.html` file from being served back as if it were an image otherwise.
    """
    directory = Path(directory)
    safe_name = Path(file.filename).name or "upload"
    if allowed_extensions is not None and Path(safe_name).suffix.lower() not in {ext.lower() for ext in allowed_extensions}:
        raise ValueError(f"Upload {safe_name!r} has a disallowed extension; allowed extensions are {tuple(allowed_extensions)}.")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / safe_name
    destination.write_bytes(file.content)
    return destination
