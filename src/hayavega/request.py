"""Framework-neutral HTTP request object."""

from __future__ import annotations

from dataclasses import dataclass
from os import urandom
from urllib.parse import parse_qs

import msgspec


def _parse_query_string(raw: str) -> dict[str, list[str]]:
    """`parse_qs`, short-circuited for the common empty-query-string case.

    `parse_qs("")` already returns `{}`, so this changes no behavior -- it
    just skips the function call (which still builds a `parse_qsl()`
    generator and iterates it) for every request with no query string,
    found by profiling a trivial route's end-to-end WSGI cost
    (`cProfile` against `benchmarks/hayavega_app/app.py`'s `/echo`, which
    has none) and seeing `parse_qs`/`parse_qsl` show up at a measurable
    share of total per-request time despite having nothing to parse.
    """
    return parse_qs(raw) if raw else {}


def generate_request_id() -> str:
    """A fast, sufficiently random request ID for requests that didn't send
    their own `X-Request-Id`.

    `uuid4().hex` was used here and in `hayavega.asgi` until profiling
    found it costing ~9x more than this per call (`uuid.UUID.__init__`
    does RFC 4122 version/variant bit manipulation and validation this
    code has no use for -- nothing downstream requires an actual UUID
    shape, only `_REQUEST_ID`'s `^[A-Za-z0-9_-]{1,64}$` pattern, which a
    plain random hex string already satisfies with the same 128 bits of
    randomness `uuid4()` provides).
    """
    return urandom(16).hex()


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
            query_params=_parse_query_string(environ.get("QUERY_STRING", "")),
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
