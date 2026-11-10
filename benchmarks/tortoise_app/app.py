"""Tortoise ORM comparison app: the async-driver `db_list` scenario this
document's roadmap flagged as new scope, deferred, not yet started
("Tortoise ORM as a new comparison target ... uses an async driver
(`aiosqlite`), a different shape of comparison than anything `benchmarks/`
currently runs -- every existing target opens a plain synchronous `sqlite3`
connection per request, Hayavega's ORM included").

Same `Post` shape (id, title, views), same `SEED_COUNT`, and the same
`/posts.json` scenario as `benchmarks/fastapi_app/app.py` and
`benchmarks/hayavega_app/app.py` -- the only difference is the database layer:
Tortoise ORM's queryset API over `aiosqlite`, awaited all the way through
the view, instead of a synchronous `sqlite3`/Hayavega-ORM call. Scoped to
just `db_list` (no `/echo`, `/posts`, or `/static/...` routes) because
that is the one scenario an async driver can plausibly change -- the other
three scenarios in `run.py`/`rust_runtime_vs_fastapi.py` don't touch the
database and are already benchmarked there.

Disclosed up front, not discovered after the fact: `aiosqlite` is not
truly asynchronous I/O. Reading its own source
(`aiosqlite/core.py`, `_connection_worker_thread`) shows every connection
runs a dedicated background `threading.Thread` that executes the real,
blocking `sqlite3` calls; `await`ing a query just parks the coroutine on a
`Future` that thread later resolves via `call_soon_threadsafe`. So this
comparison is really "one dedicated worker thread per connection" (Tortoise
ORM/aiosqlite) vs. "N shared threads across every connection" (Hayavega's own
ORM, dispatched to ASGI's sync-dispatch pool or a WSGI worker thread) --
not "async I/O vs. blocking I/O" in the sense `benchmarks/asgi_concurrent_io.py`
tested for a real network call. That distinction matters for what a result
here does and doesn't establish, and is why this comparison needed its own
scoping before being built rather than being folded into an existing app.

Run standalone: uvicorn benchmarks.tortoise_app.app:application --port 9011
Served for real benchmark runs by benchmarks/tortoise_vs_hayavega.py.
"""

import tempfile
from pathlib import Path

from fastapi import FastAPI
from tortoise import Tortoise, fields
from tortoise.models import Model

DATABASE = Path(tempfile.gettempdir()) / "tortoise_benchmark.sqlite3"
SEED_COUNT = 20


class Post(Model):
    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=255)
    views = fields.IntField()

    class Meta:
        table = "posts"


async def seed() -> None:
    if await Post.all().count() >= SEED_COUNT:
        return
    await Post.bulk_create([Post(title=f"Post {i}", views=i * 3) for i in range(SEED_COUNT)])


application = FastAPI(title="Benchmark", version="1.0.0")


@application.get("/posts.json")
async def posts_json():
    posts = await Post.all().order_by("id").limit(SEED_COUNT).values("id", "title", "views")
    return posts


@application.get("/echo")
async def echo():
    # Not one of run.py's cross-framework scenarios (see module docstring) --
    # kept only as a plain, DB-free liveness check for wait_until_ready() in
    # benchmarks/tortoise_vs_hayavega.py, the same role it plays in every other
    # benchmark app in this directory.
    return {"status": "ok"}


@application.on_event("startup")
async def on_startup():
    # _enable_global_fallback=True: Tortoise 1.x's default context manager
    # (`async with Tortoise.init(...) as ctx:`) scopes connections to that
    # `with` block, which doesn't fit a FastAPI startup/shutdown pair --
    # this opts into the pre-1.0 global-state behavior every other
    # framework's Tortoise integration (including its own `FastAPI`
    # extension) still uses for exactly this reason.
    await Tortoise.init(db_url=f"sqlite://{DATABASE}", modules={"models": ["benchmarks.tortoise_app.app"]}, _enable_global_fallback=True)
    await Tortoise.generate_schemas(safe=True)
    await seed()


@application.on_event("shutdown")
async def on_shutdown():
    await Tortoise.close_connections()
