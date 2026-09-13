"""FastAPI comparison app: JSON echo, DB-backed list, template render, static file.

Run standalone: uvicorn benchmarks.fastapi_app.app:application --port 9002
Served for real benchmark runs by benchmarks/run.py.
"""

import sqlite3
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(tempfile.gettempdir()) / "fastapi_benchmark.sqlite3"
SEED_COUNT = 20

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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


@application.on_event("startup")
def on_startup():
    seed()


if __name__ == "__main__":
    import uvicorn

    seed()
    uvicorn.run(application, host="127.0.0.1", port=9002)
