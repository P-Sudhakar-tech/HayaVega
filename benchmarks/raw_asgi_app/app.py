"""Raw ASGI baseline app: no framework at all -- the "honest ceiling" target.

JSON echo, DB-backed list, template-free render, static file -- same four
routes and same seeded SQLite schema as benchmarks/fastapi_app/app.py, but
dispatched by hand and rendered without Jinja2, so this is the cost floor
every framework in the suite is measured against.

Run standalone: uvicorn benchmarks.raw_asgi_app.app:application --port 9005
Served for real benchmark runs by benchmarks/run.py.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(tempfile.gettempdir()) / "raw_asgi_benchmark.sqlite3"
SEED_COUNT = 20

_STATIC_SVG = (BASE_DIR / "static" / "logo.svg").read_bytes()
_PAGE_TEMPLATE = (BASE_DIR / "templates" / "page.html").read_text()


def seed() -> None:
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, views INTEGER NOT NULL)")
        (count,) = connection.execute("SELECT COUNT(*) FROM posts").fetchone()
        if count < SEED_COUNT:
            connection.executemany("INSERT INTO posts (title, views) VALUES (?, ?)", [(f"Post {i}", i * 3) for i in range(SEED_COUNT)])
            connection.commit()
    finally:
        connection.close()


def fetch_posts() -> list[dict]:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT id, title, views FROM posts ORDER BY id LIMIT ?", (SEED_COUNT,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


async def _drain_body(receive) -> None:
    """Read the request body to completion, per the ASGI spec, even though
    none of this app's routes use it -- a well-behaved app always drains it."""
    more_body = True
    while more_body:
        message = await receive()
        more_body = message.get("more_body", False)


async def _send_response(send, status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _echo(send) -> None:
    body = json.dumps({"status": "ok"}).encode("utf-8")
    await _send_response(send, 200, [(b"content-type", b"application/json")], body)


async def _posts_json(send) -> None:
    body = json.dumps(fetch_posts()).encode("utf-8")
    await _send_response(send, 200, [(b"content-type", b"application/json")], body)


async def _posts_page(send) -> None:
    items = "\n".join(f"<li>{post['title']} ({post['views']} views)</li>" for post in fetch_posts())
    body = _PAGE_TEMPLATE.format(items=items).encode("utf-8")
    await _send_response(send, 200, [(b"content-type", b"text/html; charset=utf-8")], body)


async def _static_logo(send) -> None:
    await _send_response(send, 200, [(b"content-type", b"image/svg+xml")], _STATIC_SVG)


async def _not_found(send) -> None:
    await _send_response(send, 404, [(b"content-type", b"text/plain; charset=utf-8")], b"Not Found")


_ROUTES = {
    ("GET", "/echo"): _echo,
    ("GET", "/posts.json"): _posts_json,
    ("GET", "/posts"): _posts_page,
    ("GET", "/static/logo.svg"): _static_logo,
}


async def application(scope: dict, receive, send) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                seed()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    assert scope["type"] == "http"
    await _drain_body(receive)

    handler = _ROUTES.get((scope["method"], scope["path"]), _not_found)
    await handler(send)


if __name__ == "__main__":
    import uvicorn

    seed()
    uvicorn.run(application, host="127.0.0.1", port=9005)
