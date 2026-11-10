"""Isolated benchmark for a real overhead found while chasing the uvicorn/
FastAPI ``template_render``/``db_list`` gap: ``Collection.prepare()`` (called
by every ``fetch``/``total``/``revise``/``erase``/``append``) opened a second
sqlite3 connection and re-ran ``CREATE TABLE IF NOT EXISTS`` on *every single
call*, on top of the connection the actual query itself opens -- unlike
``benchmarks/fastapi_app/app.py``'s ``fetch_posts()``, which opens exactly
one connection and runs exactly one statement. This never showed up in any
apples-to-apples framework comparison because the comparison apps don't use
Hayavega's ORM; it only shows up when Hayavega's own `fetch()` is measured
against what it could be.

Run: python benchmarks/orm_prepare_overhead.py
"""

import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hayavega import Entity, Integer, Text  # noqa: E402


class Post(Entity):
    title = Text()
    views = Integer()


def main() -> None:
    db_path = Path(tempfile.gettempdir()) / "hayavega_bench_prepare_overhead.sqlite3"
    if db_path.exists():
        db_path.unlink()

    collection = Post.collection(db_path)
    for i in range(20):
        collection.append(title=f"Post {i}", views=i * 3)

    calls = 2000

    started = time.perf_counter()
    for _ in range(calls):
        Post.collection(db_path).browse().arrange("id").window(20).fetch()
    elapsed_orm = time.perf_counter() - started

    def fetch_posts_raw() -> list[dict]:
        connection = sqlite3.connect(str(db_path))
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT id, title, views FROM post_entries ORDER BY id LIMIT 20").fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    started = time.perf_counter()
    for _ in range(calls):
        fetch_posts_raw()
    elapsed_raw = time.perf_counter() - started

    print(f"Post.collection(...).fetch(): {elapsed_orm / calls * 1000:.4f} ms/call ({elapsed_orm:.3f}s / {calls} calls)")
    print(f"Raw sqlite3 (FastAPI-shaped): {elapsed_raw / calls * 1000:.4f} ms/call ({elapsed_raw:.3f}s / {calls} calls)")
    print(f"Ratio: {elapsed_orm / elapsed_raw:.2f}x")


if __name__ == "__main__":
    main()
