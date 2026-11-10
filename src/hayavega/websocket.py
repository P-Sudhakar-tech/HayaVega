"""Minimal ASGI WebSocket support for Hayavega.

WebSocket connections only exist under ``hayavega.asgi`` -- WSGI (the default
transport, ``hayavega.runtime``) has no equivalent scope type, so a WSGI
application never sees one and this module has nothing to do with it.

Register a handler with ``websocket_path()`` (mirrors ``routing.path()``) and
pass a list of them to ``create_asgi_application(websocket_patterns=[...])``:

```python
from hayavega.websocket import websocket_path, WebSocketDisconnect

async def echo(ws):
    await ws.accept()
    try:
        while True:
            await ws.send_text(await ws.receive_text())
    except WebSocketDisconnect:
        pass

websocket_patterns = [websocket_path("ws/echo", echo)]
```

A handler is one ``async def`` coroutine per connection: it owns the
connection's whole lifetime, from ``accept()`` (or rejecting by never
accepting) to whenever it returns or a ``WebSocketDisconnect`` propagates out
of a ``receive_*`` call. There is no per-message routing or middleware chain
here -- deliberately small, matching the rest of this framework's dependency-
free, hand-rolled-over-imported philosophy (`templates.py`, `graphql.py`).

Only exact-path routes are supported (no ``<path>`` wildcard segment, unlike
HTTP routing) -- a connection's path is typically one fixed endpoint
(``/ws/chat``, ``/ws/notifications``), not a resource hierarchy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

import msgspec


class WebSocketDisconnect(Exception):
    """Raised by ``WebSocket.receive_*`` once the client has disconnected.

    A handler's normal shutdown path is to catch this around its receive
    loop and return -- not treated as an error, and never logged as one by
    ``hayavega.asgi``'s dispatcher.
    """

    def __init__(self, code: int = 1000):
        self.code = code
        super().__init__(f"WebSocket disconnected with code {code}")


@dataclass(frozen=True)
class WebSocketRoute:
    pattern: str
    handler: Callable[[WebSocket], Any]


def websocket_path(pattern: str, handler: Callable[[WebSocket], Any]) -> WebSocketRoute:
    """Register a WebSocket route. ``handler`` is ``async def handler(ws: WebSocket) -> None``."""
    return WebSocketRoute(pattern.strip("/"), handler)


class WebSocket:
    """A thin wrapper over the raw ASGI websocket ``scope``/``receive``/``send`` callables.

    Mirrors the shape of this framework's HTTP ``Request``/``Response``
    objects: a small, explicit surface rather than a full framework
    abstraction. Text and JSON are the two supported message shapes (binary
    frames are not exposed) -- consistent with how the rest of Hayavega treats
    bodies (``msgspec``-encoded JSON, or plain text/HTML).

    ``hayavega.asgi`` constructs this and always consumes the connection's
    leading ``websocket.connect`` message itself before calling the route
    handler, so a handler can go straight to ``accept()`` (or reject the
    connection by returning without ever calling it, which closes the
    socket with the default ASGI close code).
    """

    def __init__(self, scope: dict, receive, send, *, max_message_bytes: int | None = None):
        self.scope = scope
        self._receive = receive
        self._send = send
        self._max_message_bytes = max_message_bytes
        self.path = scope.get("path", "/")
        query_string = scope.get("query_string", b"").decode("latin-1")
        self.query_params = parse_qs(query_string)
        self.headers = {raw_name.decode("latin-1").title(): raw_value.decode("latin-1") for raw_name, raw_value in scope.get("headers", ())}
        client = scope.get("client") or ("", 0)
        self.remote_addr = client[0]
        self.accepted = False
        self.closed = False

    async def accept(self, *, subprotocol: str | None = None) -> None:
        message: dict[str, Any] = {"type": "websocket.accept"}
        if subprotocol is not None:
            message["subprotocol"] = subprotocol
        await self._send(message)
        self.accepted = True

    async def receive_text(self) -> str:
        message = await self._receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        text = message.get("text") or ""
        if self._max_message_bytes is not None and len(text.encode("utf-8")) > self._max_message_bytes:
            await self.close(code=1009)
            raise WebSocketDisconnect(1009)
        return text

    async def receive_json(self) -> Any:
        return msgspec.json.decode(await self.receive_text())

    async def send_text(self, data: str) -> None:
        await self._send({"type": "websocket.send", "text": data})

    async def send_json(self, data: Any) -> None:
        await self.send_text(msgspec.json.encode(data).decode("utf-8"))

    async def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        await self._send({"type": "websocket.close", "code": code})
        self.closed = True
