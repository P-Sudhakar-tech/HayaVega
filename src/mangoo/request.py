"""Framework-neutral HTTP request object."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs

import msgspec


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
        """Parse an ``application/x-www-form-urlencoded`` body; last value wins per field."""
        return {name: values[-1] for name, values in parse_qs(self.body.decode("utf-8")).items()}
