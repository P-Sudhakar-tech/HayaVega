"""Stand-in for "another service" a real view calls out to over the network.

A raw ASGI callable, not a framework app -- the point is to add a realistic,
controllable amount of network-bound latency (an `await asyncio.sleep`, not
CPU work) without a framework's own request-handling overhead confounding
the delay it exists to add. Every benchmarked view (Mangoo async, Mangoo
sync/blocking, FastAPI async, FastAPI sync/blocking -- see
benchmarks/asgi_concurrent_io.py) calls this same upstream identically, so
it is the one shared, controlled variable behind the "does ASGI's
concurrent I/O actually help" question.

Run standalone: uvicorn benchmarks.upstream_app.app:application --port 9010
Delay is configurable via MANGOO_BENCH_UPSTREAM_DELAY_MS (default 40).
"""

from __future__ import annotations

import asyncio
import os

DELAY_SECONDS = int(os.environ.get("MANGOO_BENCH_UPSTREAM_DELAY_MS", "40")) / 1000
_BODY = b'{"ok": true}'
# A declared content-length, for a real server's sake -- uvicorn's h11
# implementation sends this response chunked on a keep-alive connection
# regardless (verified independently with stdlib http.client, not just this
# project's own client), so benchmarks/_upstream_client.py has to handle
# chunked transfer-encoding either way; this header costs nothing to keep.
_HEADERS = [(b"content-type", b"application/json"), (b"content-length", str(len(_BODY)).encode("ascii"))]


async def application(scope: dict, receive, send) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return
    assert scope["type"] == "http"
    await asyncio.sleep(DELAY_SECONDS)
    await send({"type": "http.response.start", "status": 200, "headers": _HEADERS})
    await send({"type": "http.response.body", "body": _BODY})


if __name__ == "__main__":
    import uvicorn

    print(f"Upstream stand-in (dev server): http://127.0.0.1:9010/delay -- {DELAY_SECONDS * 1000:.0f}ms delay")
    uvicorn.run(application, host="127.0.0.1", port=9010)
