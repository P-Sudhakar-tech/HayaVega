"""Robyn comparison app: JSON echo, DB-backed list, template-free render,
static file -- same four routes and same seeded SQLite schema as
benchmarks/raw_asgi_app/app.py, built with Robyn (a Rust-core Python web
framework) instead of hand-written ASGI.

Robyn has no built-in templating, so /posts is rendered the same way
benchmarks/raw_asgi_app/app.py renders it: an f-string/str.format loop, for
the same honest-comparison reason that app uses one.

Robyn's serving model is its own, not ASGI -- there is no external server
(uvicorn/waitress) hosting this app. The app starts itself via
``app.start(host=..., port=...)``.

Run standalone: python benchmarks/robyn_app/app.py
Served for real benchmark runs by benchmarks/run.py.
"""

import sqlite3
import tempfile
from pathlib import Path

from robyn import Response, Robyn

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(tempfile.gettempdir()) / "robyn_benchmark.sqlite3"
SEED_COUNT = 20

_PAGE_TEMPLATE = """<!doctype html>
<html>
<head><title>Posts</title></head>
<body>
<h1>Posts</h1>
<ul>
{items}
</ul>
</body>
</html>
"""


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


app = Robyn(__file__)
app.serve_directory(route="/static", directory_path=str(BASE_DIR / "static"))


@app.get("/echo")
def echo():
    return {"status": "ok"}


@app.get("/posts.json")
def posts_json():
    return fetch_posts()


@app.get("/posts")
def posts_page():
    items = "\n".join(f"<li>{post['title']} ({post['views']} views)</li>" for post in fetch_posts())
    body = _PAGE_TEMPLATE.format(items=items)
    return Response(
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        description=body,
    )


if __name__ == "__main__":
    seed()
    app.start(host="127.0.0.1", port=9006)
