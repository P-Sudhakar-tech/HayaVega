"""Blacksheep comparison app: JSON echo, DB-backed list, template render,
static file -- same four routes and same seeded SQLite schema as
benchmarks/fastapi_app/app.py, built with Blacksheep (a fast pure-Python
ASGI framework) instead of FastAPI/Starlette.

Blacksheep apps are plain ASGI -- `application` below is a real ASGI 3.0
callable, served by uvicorn exactly like benchmarks/fastapi_app is (unlike
Robyn in benchmarks/robyn_app, which serves itself).

Blacksheep ships its own Jinja2 integration (blacksheep.server.rendering.
jinja2), so /posts uses that -- the real templating story for this
framework -- rather than the no-templating stand-in used for Robyn/raw ASGI
in benchmarks/robyn_app and benchmarks/raw_asgi_app.

Run standalone: uvicorn benchmarks.blacksheep_app.app:application --port 9007
Served for real benchmark runs by benchmarks/run.py.
"""

import sqlite3
import tempfile
from pathlib import Path

from blacksheep import Application, get
from blacksheep.server.responses import view
from blacksheep.server.rendering.jinja2 import JinjaRenderer
from blacksheep.settings.html import html_settings
from jinja2 import FileSystemLoader

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(tempfile.gettempdir()) / "blacksheep_benchmark.sqlite3"
SEED_COUNT = 20

html_settings.use(JinjaRenderer(loader=FileSystemLoader(str(BASE_DIR / "templates"))))


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


application = Application()
application.serve_files(str(BASE_DIR / "static"), root_path="static")


@get("/echo")
def echo():
    return {"status": "ok"}


@get("/posts.json")
def posts_json():
    return fetch_posts()


@get("/posts")
def posts_page():
    return view("page", {"posts": fetch_posts()})


@application.on_start
async def on_start():
    seed()


if __name__ == "__main__":
    import uvicorn

    seed()
    uvicorn.run(application, host="127.0.0.1", port=9007)
