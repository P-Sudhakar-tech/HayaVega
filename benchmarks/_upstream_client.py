"""Minimal raw-socket async HTTP/1.1 GET client with connection reuse,
shared by benchmarks/hayavega_asgi_app/app.py and benchmarks/fastapi_app/app.py's
`remote_io` views.

Not `httpx.AsyncClient`, deliberately: httpx 0.28.1 / httpcore 1.0.9 was
found, and disclosed, to serialize concurrent requests on this dev
environment rather than overlap them. See
benchmarks/asgi_concurrent_io.py's module docstring and
benchmarks/README.md for the full diagnosis.

Two real bugs were found and fixed while building this, not shipped blind:

1. **A first cut sent `Connection: close`**, opening a fresh TCP connection
   per call. Under real concurrency that reproduced the exact Windows
   ephemeral-port/`TIME_WAIT` pileup this project already diagnosed twice
   before (the Django benchmark target, and the Rust runtime's own HTTP
   keep-alive bug -- see `benchmarks/README.md#the-rust-runtime-vs-the-
   python-runtime-waitress`). `netstat` during a bad run showed dozens of
   `127.0.0.1:9010` connections sitting in `FIN_WAIT_2`. Fixed by keeping a
   small per-`(host, port)` pool of already-connected sockets and reusing
   them, the same "one connection per client, kept alive" mechanism
   `hayavega.db.enable_connection_pooling()` uses for the database side.
2. **A keep-alive response still failed to parse**, even after that fix:
   uvicorn's `h11` implementation sends `benchmarks/upstream_app`'s
   response with `Transfer-Encoding: chunked` on a persistent connection
   regardless of the app declaring a `Content-Length` header -- verified
   independently with stdlib `http.client`, not just this module, so it is
   uvicorn's real behavior here, not a bug to work around on the server
   side. This client parses both `Content-Length` and chunked framing so
   it handles whichever framing a real upstream actually sends, the same
   as any real HTTP/1.1 client would need to.
"""

from __future__ import annotations

import asyncio
import json

_REQUEST = b"GET /delay HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
_pools: dict[tuple[str, int], asyncio.Queue] = {}


def _pool_for(host: str, port: int) -> asyncio.Queue:
    key = (host, port)
    pool = _pools.get(key)
    if pool is None:
        pool = asyncio.Queue()
        _pools[key] = pool
    return pool


async def _read_chunked_body(reader: asyncio.StreamReader) -> bytes:
    chunks = []
    while True:
        size_line = await reader.readuntil(b"\r\n")
        size = int(size_line.strip(), 16)
        if size == 0:
            await reader.readuntil(b"\r\n")  # trailing CRLF after the final "0" chunk (no trailers expected)
            break
        chunks.append(await reader.readexactly(size))
        await reader.readexactly(2)  # each chunk's own trailing CRLF
    return b"".join(chunks)


async def fetch_upstream(host: str = "127.0.0.1", port: int = 9010) -> dict:
    pool = _pool_for(host, port)
    try:
        reader, writer = pool.get_nowait()
    except asyncio.QueueEmpty:
        reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(_REQUEST)
        await writer.drain()
        header_bytes = await reader.readuntil(b"\r\n\r\n")
        headers = header_bytes.decode("latin-1").lower()
        if "transfer-encoding: chunked" in headers:
            body = await _read_chunked_body(reader)
        else:
            content_length = 0
            for line in headers.split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip() == "content-length":
                    content_length = int(value.strip())
                    break
            body = await reader.readexactly(content_length) if content_length else b""
    except Exception:
        writer.close()
        raise
    pool.put_nowait((reader, writer))
    return json.loads(body)
