"""Dependency-free WSGI and ASGI test clients for Hayavega applications."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import msgspec


@dataclass(frozen=True)
class TestResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return msgspec.json.decode(self.content)


class TestClient:
    def __init__(self, application):
        self.application, self.cookies = application, {}

    def request(self, method: str, path: str, *, json_data=None, form_data=None, headers=None):
        captured: dict[str, Any] = {}
        body = b""

        def start_response(status, response_headers):
            captured.update(status=status, headers=dict(response_headers))

        if json_data is not None:
            payload = msgspec.json.encode(json_data)
        elif form_data is not None:
            payload = urlencode(form_data).encode("utf-8")
        else:
            payload = b""
        merged = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()), **(headers or {})}
        path_info, _, query_string = path.partition("?")
        environ = {"PATH_INFO": path_info, "QUERY_STRING": query_string, "REQUEST_METHOD": method.upper(), "wsgi.input": io.BytesIO(payload), "CONTENT_LENGTH": str(len(payload)), **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in merged.items() if v}}
        if json_data is not None:
            environ["CONTENT_TYPE"] = "application/json"
        elif form_data is not None:
            environ["CONTENT_TYPE"] = "application/x-www-form-urlencoded"
        body = b"".join(self.application(environ, start_response))
        cookie = captured["headers"].get("Set-Cookie", "").split(";", 1)[0]
        if "=" in cookie:
            key, value = cookie.split("=", 1)
            self.cookies[key] = value
        return TestResponse(int(captured["status"].split()[0]), captured["headers"], body)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


class WebSocketClosed(Exception):
    """Raised by ``WebSocketTestSession.receive_text``/``receive_json`` once the server has closed the connection."""

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"WebSocket closed by the server with code {code}")


class WebSocketTestSession:
    """Drives one WebSocket connection against a Hayavega ASGI application in-process.

    Symmetric to ``AsyncTestClient``: no real network socket, just the same
    ``scope``/``receive``/``send`` contract an ASGI server itself drives.
    Unlike an HTTP request/response, a WebSocket connection is held open
    across many messages in both directions, so this drives the
    application's handler coroutine as a background ``asyncio.Task`` talking
    over two queues, rather than one call-and-return like
    ``AsyncTestClient.request``. Use as an async context manager:

    ```python
    async with connect_websocket(application, "/ws/echo") as session:
        await session.send_text("hi")
        assert await session.receive_text() == "hi"
    ```

    Entering the context sends the connection's ``websocket.connect``
    message and waits for the application to respond with either
    ``websocket.accept`` (``session.accepted`` is ``True``) or an immediate
    ``websocket.close`` (``session.close_code`` is set, ``accepted`` is
    ``False`` -- the shape a rejected connection, e.g. a failed host/origin
    check, takes). Exiting the context sends ``websocket.disconnect`` if the
    server hasn't already closed the connection, then awaits the handler
    task so any exception it raised propagates to the test.
    """

    def __init__(self, application, path: str, *, headers: dict[str, str] | None = None):
        self.application = application
        self.path = path
        self.headers = headers or {}
        self.accepted = False
        self.close_code: int | None = None
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> WebSocketTestSession:
        path_info, _, query_string = self.path.partition("?")
        scope = {
            "type": "websocket",
            "path": path_info,
            "query_string": query_string.encode("latin-1"),
            "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in self.headers.items()],
            "client": ("127.0.0.1", 0),
        }

        async def receive():
            return await self._to_app.get()

        async def send(message):
            await self._from_app.put(message)

        self._task = asyncio.create_task(self.application(scope, receive, send))
        await self._to_app.put({"type": "websocket.connect"})
        first = await self._from_app.get()
        if first["type"] == "websocket.accept":
            self.accepted = True
        elif first["type"] == "websocket.close":
            self.close_code = first.get("code", 1000)
        else:
            raise AssertionError(f"Expected websocket.accept or websocket.close first, got {first['type']!r}")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.close_code is None:
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            await self._task

    async def send_text(self, data: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": data})

    async def send_json(self, data) -> None:
        await self.send_text(msgspec.json.encode(data).decode("utf-8"))

    async def receive_text(self) -> str:
        message = await self._from_app.get()
        if message["type"] == "websocket.close":
            self.close_code = message.get("code", 1000)
            raise WebSocketClosed(self.close_code)
        return message.get("text") or ""

    async def receive_json(self):
        return msgspec.json.decode(await self.receive_text())


def connect_websocket(application, path: str, *, headers: dict[str, str] | None = None) -> WebSocketTestSession:
    return WebSocketTestSession(application, path, headers=headers)


class AsyncTestClient:
    """Dependency-free test client for Hayavega ASGI applications (see ``hayavega.asgi``).

    Each call to ``request``/``get``/``post`` is synchronous from the
    caller's perspective -- it drives one ``asyncio.run()`` internally --
    matching this project's existing ``unittest``-based test style rather
    than requiring an async test runner or a pytest plugin.
    """

    def __init__(self, application):
        self.application, self.cookies = application, {}

    def request(self, method: str, path: str, *, json_data=None, form_data=None, headers=None) -> TestResponse:
        return asyncio.run(self._request(method, path, json_data=json_data, form_data=form_data, headers=headers))

    async def _request(self, method: str, path: str, *, json_data=None, form_data=None, headers=None) -> TestResponse:
        if json_data is not None:
            payload = msgspec.json.encode(json_data)
        elif form_data is not None:
            payload = urlencode(form_data).encode("utf-8")
        else:
            payload = b""
        merged = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()), **(headers or {})}
        if json_data is not None:
            merged.setdefault("Content-Type", "application/json")
        elif form_data is not None:
            merged.setdefault("Content-Type", "application/x-www-form-urlencoded")
        merged.setdefault("Content-Length", str(len(payload)))
        path_info, _, query_string = path.partition("?")
        scope = {
            "type": "http",
            "method": method.upper(),
            "path": path_info,
            "query_string": query_string.encode("latin-1"),
            "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in merged.items() if value],
            "client": ("127.0.0.1", 0),
            "scheme": "http",
        }
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}

        messages: list[dict[str, Any]] = []

        async def send(message):
            messages.append(message)

        await self.application(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        # ASGI header names are always lowercase bytes per spec (see hayavega.asgi).
        response_headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in start["headers"]}
        cookie = response_headers.get("set-cookie", "").split(";", 1)[0]
        if "=" in cookie:
            key, value = cookie.split("=", 1)
            self.cookies[key] = value
        return TestResponse(start["status"], response_headers, content)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)
