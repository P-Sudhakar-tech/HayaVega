"""Framework-neutral HTTP request object."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs

import msgspec


@dataclass(frozen=True)
class UploadedFile:
    """One uploaded file from a `multipart/form-data` submission."""

    field_name: str
    filename: str
    content_type: str | None
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)


class MultipartUnavailableError(RuntimeError):
    """Raised when parsing a `multipart/form-data` body without `python-multipart` installed."""


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, list[UploadedFile]]]:
    """Parse a complete in-memory multipart body into plain fields and uploaded files.

    Requires `python-multipart` (`uv add python-multipart`) -- kept
    optional, the same policy as the `cryptography` dependency for TOTP
    encryption, rather than a mandatory dependency for every project,
    including ones that never accept file uploads.
    """
    try:
        from python_multipart import create_form_parser
    except ImportError as error:
        raise MultipartUnavailableError("Parsing multipart/form-data requires `python-multipart` -- `uv add python-multipart`.") from error

    fields: dict[str, str] = {}
    files: dict[str, list[UploadedFile]] = {}

    def on_field(field) -> None:
        name = field.field_name.decode("utf-8") if field.field_name else ""
        fields[name] = (field.value or b"").decode("utf-8", errors="replace")

    def on_file(file) -> None:
        name = file.field_name.decode("utf-8") if file.field_name else ""
        filename = file.file_name.decode("utf-8", errors="replace") if file.file_name else ""
        file.file_object.seek(0)
        uploaded = UploadedFile(field_name=name, filename=filename, content_type=file.content_type, content=file.file_object.read())
        files.setdefault(name, []).append(uploaded)

    parser = create_form_parser({"Content-Type": content_type.encode("latin-1")}, on_field, on_file)
    parser.write(body)
    parser.finalize()
    return fields, files


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query_params: dict[str, list[str]]
    body: bytes
    headers: dict[str, str]
    remote_addr: str = ""
    is_secure: bool = False
    raw_query: str = ""

    @property
    def cookies(self) -> dict[str, str]:
        """Return safely parsed request cookies."""
        values = {}
        for item in self.headers.get("Cookie", "").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name:
                values[name] = value
        return values

    @classmethod
    def from_wsgi(cls, environ: dict, *, read_body: bool = True) -> Request:
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(content_length) if read_body and content_length else b""
        headers = {key[5:].replace("_", "-").title(): value for key, value in environ.items() if key.startswith("HTTP_")}
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        return cls(
            method=environ.get("REQUEST_METHOD", "GET").upper(),
            path=environ.get("PATH_INFO", "/"),
            query_params=parse_qs(environ.get("QUERY_STRING", "")),
            body=body,
            headers=headers,
            remote_addr=environ.get("REMOTE_ADDR", ""),
            is_secure=environ.get("wsgi.url_scheme", "http").lower() == "https",
            raw_query=environ.get("QUERY_STRING", ""),
        )

    def json(self):
        return msgspec.json.decode(self.body)

    def form(self) -> dict[str, str]:
        """Parse a form body into plain fields -- `last value wins per
        field. Handles `application/x-www-form-urlencoded` (the
        default) and `multipart/form-data` (needs `python-multipart`;
        see `files()`)."""
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            fields, _ = _parse_multipart(content_type, self.body)
            return fields
        return {name: values[-1] for name, values in parse_qs(self.body.decode("utf-8")).items()}

    def files(self) -> dict[str, UploadedFile]:
        """Parse a `multipart/form-data` body into uploaded files, keyed
        by field name (the first file, if more than one was submitted
        under the same field name). Returns `{}` for any other content
        type. Requires `python-multipart` -- see `MultipartUnavailableError`.
        """
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            return {}
        _, files = _parse_multipart(content_type, self.body)
        return {name: items[0] for name, items in files.items()}
