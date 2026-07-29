"""Dependency-free WSGI test client for Mangoo applications."""
from __future__ import annotations

from dataclasses import dataclass
import io
import json


@dataclass(frozen=True)
class TestResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    @property
    def text(self): return self.content.decode("utf-8")
    def json(self): return json.loads(self.text)


class TestClient:
    def __init__(self, application): self.application, self.cookies = application, {}
    def request(self, method: str, path: str, *, json_data=None, headers=None):
        captured, body = {}, b""
        def start_response(status, response_headers): captured.update(status=status, headers=dict(response_headers))
        payload = json.dumps(json_data).encode() if json_data is not None else b""
        merged = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()), **(headers or {})}
        environ = {"PATH_INFO": path, "REQUEST_METHOD": method.upper(), "wsgi.input": io.BytesIO(payload), "CONTENT_LENGTH": str(len(payload)), **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in merged.items() if v}}
        if json_data is not None: environ["CONTENT_TYPE"] = "application/json"
        body = b"".join(self.application(environ, start_response))
        cookie = captured["headers"].get("Set-Cookie", "").split(";", 1)[0]
        if "=" in cookie:
            key, value = cookie.split("=", 1); self.cookies[key] = value
        return TestResponse(int(captured["status"].split()[0]), captured["headers"], body)
    def get(self, path, **kwargs): return self.request("GET", path, **kwargs)
    def post(self, path, **kwargs): return self.request("POST", path, **kwargs)
