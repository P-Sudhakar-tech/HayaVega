"""Framework-neutral HTTP request object."""
from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import parse_qs


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query_params: dict[str, list[str]]
    body: bytes
    headers: dict[str, str]

    @classmethod
    def from_wsgi(cls, environ: dict) -> "Request":
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ.get("wsgi.input").read(content_length) if content_length else b""
        headers = {
            key[5:].replace("_", "-").title(): value
            for key, value in environ.items()
            if key.startswith("HTTP_")
        }
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        return cls(
            method=environ.get("REQUEST_METHOD", "GET").upper(),
            path=environ.get("PATH_INFO", "/"),
            query_params=parse_qs(environ.get("QUERY_STRING", "")),
            body=body,
            headers=headers,
        )

    def json(self):
        return json.loads(self.body.decode("utf-8"))
