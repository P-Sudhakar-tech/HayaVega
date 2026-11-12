"""Connection-pooling throughput, measured against real PostgreSQL and
MySQL, not just SQLite.

`hayavega.db.enable_connection_pooling()` was benchmarked exhaustively for
SQLite (see INDUSTRY_LEADERSHIP_ROADMAP.md's "Connection pooling,
attempted" section) -- a real win under ASGI/uvicorn, a real ~10-15%
regression under WSGI/waitress, both measured at the HTTP level and
reproduced in an isolated, framework-free multi-threaded microbenchmark.
The same rigor had never been applied to the PostgreSQL/MySQL paths,
which only had correctness coverage (tests/test_postgresql_integration.py,
tests/test_mysql_integration.py), not a throughput measurement under
concurrency.

This mirrors that SQLite investigation's own framework-free methodology
directly: N threads, each opening (unpooled) or reusing (pooled) one
connection and running a fixed number of simple parameterized queries in
a loop, timed end to end. Not an HTTP-level benchmark -- there is no
benchmarks/postgres_app or benchmarks/mysql_app serving these over a real
WSGI/ASGI server, matching the scope the SQLite work's own isolated
microbenchmark already established as sufficient to attribute an effect
to connection reuse itself, not to anything server- or framework-specific.

Needs the same opt-in environment variables as
tests/test_postgresql_integration.py / tests/test_mysql_integration.py
(HAYAVEGA_TEST_POSTGRES_*/HAYAVEGA_TEST_MYSQL_*) -- CI's existing
postgres:16/mysql:8 service containers, already wired for the
correctness tests, are reused here rather than requiring a new
environment. Skips whichever engine isn't configured, rather than
failing, so this can run against just one or both.

Usage:
    python benchmarks/connection_pooling_postgres_mysql.py [--threads 16] [--queries 200]
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hayavega.db import DatabaseDriverError, connect, disable_connection_pooling, enable_connection_pooling  # noqa: E402


def _postgres_database() -> dict | None:
    if not os.getenv("HAYAVEGA_TEST_POSTGRES_NAME"):
        return None
    return {
        "ENGINE": "postgresql",
        "NAME": os.environ["HAYAVEGA_TEST_POSTGRES_NAME"],
        "HOST": os.getenv("HAYAVEGA_TEST_POSTGRES_HOST", "localhost"),
        "PORT": int(os.getenv("HAYAVEGA_TEST_POSTGRES_PORT", "5432")),
        "USER": os.getenv("HAYAVEGA_TEST_POSTGRES_USER"),
        "PASSWORD": os.getenv("HAYAVEGA_TEST_POSTGRES_PASSWORD"),
    }


def _mysql_database() -> dict | None:
    if not os.getenv("HAYAVEGA_TEST_MYSQL_NAME"):
        return None
    return {
        "ENGINE": "mysql",
        "NAME": os.environ["HAYAVEGA_TEST_MYSQL_NAME"],
        "HOST": os.getenv("HAYAVEGA_TEST_MYSQL_HOST", "localhost"),
        "PORT": int(os.getenv("HAYAVEGA_TEST_MYSQL_PORT", "3306")),
        "USER": os.getenv("HAYAVEGA_TEST_MYSQL_USER"),
        "PASSWORD": os.getenv("HAYAVEGA_TEST_MYSQL_PASSWORD"),
    }


def _worker_pooled(database: dict, queries: int) -> None:
    for _ in range(queries):
        connection = connect(database)
        connection.execute("SELECT 1")
        connection.close()  # pooled: rollback() and keep this thread's connection open


def _worker_unpooled(database: dict, queries: int) -> None:
    for _ in range(queries):
        connection = connect(database)
        connection.execute("SELECT 1")
        connection.close()  # unpooled: a real close() every call


def _run(database: dict, *, threads: int, queries: int, pooled: bool) -> float:
    worker = _worker_pooled if pooled else _worker_unpooled
    if pooled:
        enable_connection_pooling()
    try:
        thread_list = [threading.Thread(target=worker, args=(database, queries)) for _ in range(threads)]
        start = time.perf_counter()
        for thread in thread_list:
            thread.start()
        for thread in thread_list:
            thread.join()
        elapsed = time.perf_counter() - start
    finally:
        disable_connection_pooling()  # also closes every connection this process pooled
    total_queries = threads * queries
    return total_queries / elapsed


def benchmark_engine(name: str, database: dict, *, threads: int, queries: int) -> None:
    try:
        probe = connect(database)
        probe.close()
    except DatabaseDriverError as error:
        print(f"{name}: skipped ({error})")
        return

    unpooled_qps = _run(database, threads=threads, queries=queries, pooled=False)
    pooled_qps = _run(database, threads=threads, queries=queries, pooled=True)
    ratio = pooled_qps / unpooled_qps
    print(f"{name}: unpooled={unpooled_qps:.0f} q/s  pooled={pooled_qps:.0f} q/s  ratio={ratio:.2f}x")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=16, help="concurrent threads (default: 16, matching the SQLite investigation's own microbenchmark)")
    parser.add_argument("--queries", type=int, default=200, help="queries per thread (default: 200)")
    args = parser.parse_args()

    postgres = _postgres_database()
    mysql = _mysql_database()
    if postgres is None and mysql is None:
        raise SystemExit("Neither HAYAVEGA_TEST_POSTGRES_NAME nor HAYAVEGA_TEST_MYSQL_NAME is set -- nothing to benchmark. See tests/test_postgresql_integration.py / tests/test_mysql_integration.py for the expected environment variables.")

    print(f"Connection pooling: unpooled vs. pooled, {args.threads} threads x {args.queries} queries each\n")
    if postgres is not None:
        benchmark_engine("PostgreSQL", postgres, threads=args.threads, queries=args.queries)
    else:
        print("PostgreSQL: skipped (HAYAVEGA_TEST_POSTGRES_NAME not set)")
    if mysql is not None:
        benchmark_engine("MySQL", mysql, threads=args.threads, queries=args.queries)
    else:
        print("MySQL: skipped (HAYAVEGA_TEST_MYSQL_NAME not set)")


if __name__ == "__main__":
    main()
