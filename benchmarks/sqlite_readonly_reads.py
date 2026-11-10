"""Isolated benchmark for `hayavega.db.connect_readonly()`, added to close part
of the WSGI `template_render`/`db_list` gap to FastAPI documented in
`INDUSTRY_LEADERSHIP_ROADMAP.md`. Connection pooling was already tried for
that gap and made WSGI worse (many threads holding a connection open
contends under SQLite on this environment -- see
`hayavega.db.enable_connection_pooling`'s docstring). This benchmark checks a
different, narrower idea: does opening the per-request connection read-only
(`sqlite3.connect("file:...?mode=ro", uri=True)`) rather than read-write beat
a plain `connect()` for `Selection.fetch()`/`values()`/`total()`, which never
write?

Compares, through the *exact same* `Selection.values()` code path (only the
connection function it calls is swapped, via monkeypatch, so every other
cost -- `prepare()`'s cache lookup, `_sql()` building, dict construction --
is identical in both arms and the only variable is the connection itself):
  1. `values()` using `connect_readonly()` (shipped)
  2. `values()` using plain `connect()` (the pre-change behavior)

...and, with no ORM/framework code at all, isolates the connection-open
difference on its own:
  3. Raw sqlite3, read-write connect (the FastAPI-shaped baseline used
     elsewhere in this document)
  4. Raw sqlite3, read-only URI connect

Single-threaded (per-call cost) and under 16-thread contention (the shape
`waitress`'s benchmark server actually runs under), matching
`benchmarks/orm_prepare_overhead.py`'s and `benchmarks/README.md`'s existing
methodology: run twice for reproducibility, report both.

Run: python benchmarks/sqlite_readonly_reads.py
"""

import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hayavega import Entity, Integer, Text  # noqa: E402
from hayavega.db import connect  # noqa: E402


class Post(Entity):
    title = Text()
    views = Integer()


def build_db(run: int) -> Path:
    db_path = Path(tempfile.gettempdir()) / f"hayavega_bench_readonly_reads_{run}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    collection = Post.collection(db_path)
    for i in range(20):
        collection.append(title=f"Post {i}", views=i * 3)
    return db_path


def make_callables(db_path: Path):
    def orm_values_readonly() -> list[dict]:
        return Post.collection(db_path).browse().arrange("id").window(20).values("title", "views")

    def orm_values_rw() -> list[dict]:
        with patch("hayavega.models.connect_readonly", connect):
            return Post.collection(db_path).browse().arrange("id").window(20).values("title", "views")

    def raw_rw() -> list[dict]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT title, views FROM post_entries ORDER BY id LIMIT 20").fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def raw_ro() -> list[dict]:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT title, views FROM post_entries ORDER BY id LIMIT 20").fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    return {
        "Selection.values() with connect_readonly() [shipped]": orm_values_readonly,
        "Selection.values() with plain connect() [pre-change]": orm_values_rw,
        "Raw sqlite3, read-write connect (FastAPI-shaped)": raw_rw,
        "Raw sqlite3, read-only URI connect": raw_ro,
    }


def bench_single_thread(fn, calls=2000) -> float:
    for _ in range(50):
        fn()
    started = time.perf_counter()
    for _ in range(calls):
        fn()
    return (time.perf_counter() - started) / calls * 1000


def bench_concurrent(fn, threads=16, per_thread=300) -> float:
    barrier = threading.Barrier(threads)
    durations = [0.0] * threads

    def worker(index: int) -> None:
        barrier.wait()
        started = time.perf_counter()
        for _ in range(per_thread):
            fn()
        durations[index] = time.perf_counter() - started

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join()
    return threads * per_thread / max(durations)


def run_once(run: int) -> None:
    db_path = build_db(run)
    callables = make_callables(db_path)
    rows = []
    for label, fn in callables.items():
        single = bench_single_thread(fn)
        concurrent = bench_concurrent(fn)
        rows.append((label, single, concurrent))
    width = max(len(label) for label, _, _ in rows)
    for label, single, concurrent in rows:
        print(f"{label:<{width}}  {single:7.4f} ms/call   {concurrent:8.0f} calls/sec (16 threads)")


def main() -> None:
    print("=== Run 1 ===")
    run_once(1)
    print("\n=== Run 2 (reproducibility check) ===")
    run_once(2)


if __name__ == "__main__":
    main()
