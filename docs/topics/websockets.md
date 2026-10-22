# WebSockets

WebSocket connections only exist under [ASGI](asgi.md) -- WSGI (the
default transport, `mangoo.runtime`) has no equivalent scope type, so
a WSGI application never sees one and `mangoo.websocket` has nothing
to do with it there.

## Registering a handler

```python
from mangoo.websocket import WebSocketDisconnect, websocket_path


async def echo(ws):
    await ws.accept()
    try:
        while True:
            await ws.send_text(await ws.receive_text())
    except WebSocketDisconnect:
        pass


websocket_patterns = [websocket_path("ws/echo", echo)]
application = create_asgi_application(urlpatterns, websocket_patterns=websocket_patterns)
```

`websocket_path(pattern, handler)` mirrors `routing.path()` -- pass
the list to `create_asgi_application(websocket_patterns=[...])`
alongside the ordinary HTTP `urlpatterns`. Only exact-path routes are
supported (no `<path>` wildcard segment, unlike HTTP routing): a
connection's path is typically one fixed endpoint (`/ws/chat`,
`/ws/notifications`), not a resource hierarchy.

## The handler contract

One `async def` coroutine per connection owns that connection's whole
lifetime -- there is no per-message routing or middleware chain,
deliberately small and matching the rest of this framework's
dependency-free, hand-rolled-over-imported philosophy
(`templates.py`, `graphql.py`):

- **Accept or reject**: call `await ws.accept()` to accept the
  connection (optionally `accept(subprotocol="...")`), or simply
  return without calling it to reject -- closes the socket with the
  default ASGI close code. `mangoo.asgi` always consumes the
  connection's leading `websocket.connect` message itself before
  calling the handler, so a handler can go straight to `accept()`.
- **Receive**: `await ws.receive_text()` / `await ws.receive_json()`
  (JSON-decoded via `msgspec`). Binary frames are not exposed --
  consistent with how the rest of Mangoo treats bodies (JSON or
  plain text/HTML).
- **Send**: `await ws.send_text(data)` / `await ws.send_json(data)`.
- **Disconnect**: a `receive_*` call raises `WebSocketDisconnect`
  once the client has gone away -- a handler's normal shutdown path
  is to catch this around its receive loop and return. It is not
  treated as an error and never logged as one by `mangoo.asgi`'s
  dispatcher.
- **Close**: `await ws.close(code=1000)` to close from the server
  side.

```python
from mangoo.websocket import WebSocket, WebSocketDisconnect


async def chat(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            message = await ws.receive_json()
            await ws.send_json({"echo": message})
    except WebSocketDisconnect:
        pass
```

`WebSocket` mirrors the shape of this framework's HTTP
`Request`/`Response` objects: `ws.path`, `ws.query_params`,
`ws.headers`, and `ws.remote_addr` are available the same way a
`Request`'s are, plus `ws.accepted`/`ws.closed` to check connection
state.

## Cross-site WebSocket hijacking protection

If `HardeningOptions`' `allowed_hosts`/`allowed_origins` are
configured, a connection with a mismatched `Host`/`Origin` header is
rejected *before* the handler is ever called -- the same protection
those settings already give HTTP requests, applied here because
WebSocket connections bypass the browser's ordinary same-origin/CORS
enforcement ("cross-site WebSocket hijacking"). A rejected connection
never reaches your handler at all; it closes immediately with a
rejection code, and `mango diagnose`/the usual hardening
configuration in [Security](security.md) covers how to set
`allowed_hosts`/`allowed_origins` in the first place.

## Testing

`mangoo.testing.connect_websocket(application, path)` drives a
connection against an ASGI application in-process -- the WebSocket
equivalent of `AsyncTestClient`:

```python
import unittest

from mangoo.testing import connect_websocket


class EchoTests(unittest.IsolatedAsyncioTestCase):
    async def test_echo_round_trips_a_message(self):
        async with connect_websocket(application, "/ws/echo") as session:
            await session.send_text("hi")
            self.assertEqual(await session.receive_text(), "hi")
```

Entering the `async with` block sends the connection's
`websocket.connect` message and waits for the application to respond
with either acceptance (`session.accepted` is `True`) or an immediate
close (`session.close_code` is set, `accepted` is `False` -- the
shape a rejected connection, e.g. a failed host/origin check, takes).
Exiting the block sends a disconnect if the server hasn't already
closed the connection, then awaits the handler so any exception it
raised propagates to the test. `session.send_text`/`send_json` and
`receive_text`/`receive_json` mirror `WebSocket`'s own methods;
`receive_text()` raises `WebSocketClosed` once the server has closed
the connection, the test-side equivalent of `WebSocketDisconnect`.

## Signals

Every connection emits a [Signals](signals.md) event when a `SignalBus`
is passed to `create_asgi_application(signals=...)`:
`websocket.connected` (path), `websocket.disconnected` (path), and
`websocket.rejected` (path, close code) for a connection turned away
by the host/origin check above.
