"""FastAPI comparison app: JSON echo, DB-backed list, template render, static file.

Run standalone: uvicorn benchmarks.fastapi_app.app:application --port 9002
Served for real benchmark runs by benchmarks/run.py.
"""

import sqlite3
import tempfile
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from benchmarks._upstream_client import fetch_upstream

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(tempfile.gettempdir()) / "fastapi_benchmark.sqlite3"
SEED_COUNT = 20
UPSTREAM_URL = "http://127.0.0.1:9010/delay"

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# Same session shape benchmarks/hayavega_app uses for its identical blocking
# route below -- see benchmarks/asgi_concurrent_io.py for why (a fair
# comparison needs the upstream call itself to cost the same on both
# sides). The async route below uses benchmarks/_upstream_client instead
# of httpx.AsyncClient -- see that module's docstring for the disclosed
# reason.
_upstream_session = requests.Session()


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


application = FastAPI(title="Benchmark", version="1.0.0")
application.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@application.get("/echo")
def echo():
    return {"status": "ok"}


@application.get("/posts.json")
def posts_json():
    return fetch_posts()


@application.get("/posts", response_class=HTMLResponse)
def posts_page(request: Request):
    return templates.TemplateResponse(request, "page.html", {"posts": fetch_posts()})


@application.get("/remote")
async def remote_io():
    """Real concurrent I/O: awaits benchmarks/upstream_app instead of blocking
    a thread for it. The direct counterpart to Hayavega (ASGI)'s `remote_io` in
    benchmarks/hayavega_asgi_app/app.py -- see benchmarks/asgi_concurrent_io.py."""
    return await fetch_upstream()


@application.get("/remote-blocking")
def remote_io_blocking():
    """The blocking contrast: a plain `def` route, so FastAPI dispatches it
    through Starlette/anyio's thread pool instead of the event loop -- the
    counterpart to Hayavega's `remote_io_blocking` in
    benchmarks/hayavega_app/app.py. See benchmarks/asgi_concurrent_io.py."""
    response = _upstream_session.get(UPSTREAM_URL, timeout=5)
    return response.json()


@application.on_event("startup")
def on_startup():
    seed()


if __name__ == "__main__":
    import uvicorn

    seed()
    uvicorn.run(application, host="127.0.0.1", port=9002)
