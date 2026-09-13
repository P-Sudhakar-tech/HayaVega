"""Pluggable DB-API database connections for Mangoo."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class DatabaseConfigurationError(ValueError):
    pass


class DatabaseDriverError(RuntimeError):
    pass


def normalize_config(database: str | Path | dict) -> dict:
    if isinstance(database, (str, Path)):
        return {"ENGINE": "sqlite3", "NAME": str(database)}
    return dict(database)


class Connection:
    def __init__(self, raw, engine: str):
        self.raw, self.engine = raw, engine
        self._transaction_depth = 0
        self.pooled = False

    def execute(self, sql: str, params=()):
        cursor = self.raw.cursor()
        cursor.execute(sql.replace("?", "%s") if self.engine in {"postgresql", "mysql"} else sql, params)
        return cursor

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    @contextmanager
    def atomic(self):
        """Run a block atomically, using savepoints for nested blocks.

        A connection must be used for all statements in the block.  SQLite,
        PostgreSQL, and MySQL/InnoDB all support the SQL used here.
        """
        depth = self._transaction_depth
        savepoint = f"mangoo_savepoint_{depth}"
        if depth == 0:
            self.execute("BEGIN")
        else:
            self.execute(f"SAVEPOINT {savepoint}")
        self._transaction_depth += 1
        try:
            yield self
        except Exception:
            if depth == 0:
                self.raw.rollback()
            else:
                self.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            if depth == 0:
                self.raw.commit()
            else:
                self.execute(f"RELEASE SAVEPOINT {savepoint}")
        finally:
            self._transaction_depth -= 1

    def close(self):
        """Release the connection.

        A pooled connection (see ``enable_connection_pooling``) is never
        actually closed here -- it stays open for the next `connect()` call
        on this same thread to reuse. `rollback()` runs in its place: safe
        to call even when nothing is uncommitted (every driver here treats
        it as a no-op in that case), and it guarantees the connection is
        left clean for its next user regardless of whether the caller
        committed, never wrote anything, or is exiting because of an
        exception raised mid-transaction -- which matters specifically
        *because* the connection now outlives this one call.
        """
        if self.pooled:
            self.raw.rollback()
            return
        self.raw.close()


_pool_enabled = False
_pool_local = threading.local()


def enable_connection_pooling() -> None:
    """Reuse one connection per (thread, database config) instead of opening
    a new one on every `connect()` call.

    Off by default; every existing caller of `connect()` -- `models.py`,
    `auth.py`, `admin.py`, `migrations.py`, `production.py`'s readiness
    check -- needs no change to benefit, since this only changes what
    `connect()` and `Connection.close()` do internally, not their contracts.

    Scoped deliberately to match Mangoo's WSGI/ASGI concurrency model: one
    connection per worker thread, matching the one-request-per-thread (WSGI)
    or one-coroutine-at-a-time-per-thread (ASGI sync views run via
    `asyncio.to_thread`) model this framework already uses, rather than a
    general bounded pool shared across threads with checkout/checkin
    queuing. That keeps the mechanism simple and avoids the cross-thread
    connection-sharing hazards DB-API connections generally aren't safe for
    (SQLite enforces this directly: a connection created with the default
    `check_same_thread=True` cannot be used from another thread at all).

    Known limitation, not solved here: a pooled MySQL/PostgreSQL connection
    that has sat idle long enough for the server to drop it (MySQL's
    `wait_timeout`, a network blip) is not detected or transparently
    reconnected -- the next call using it fails with a real error. This
    does not affect SQLite, which has no server-side idle timeout; for
    long-running MySQL/PostgreSQL deployments, restarting worker processes
    periodically is the simple mitigation until a real bounded pool with
    health checks is built as its own, separate piece of work.

    **Benchmark this for your own deployment before enabling it -- it is
    not a universal win.** Measured against `benchmarks/`'s SQLite-backed
    `db_list`/`template_render` scenarios: a real, substantial improvement
    under `mangoo.asgi` + uvicorn (`db_list` +15-30%, and it is what pushed
    `template_render` past FastAPI for the first time), but a consistent
    ~10-15% *regression* under the WSGI path (`waitress`, 16 threads) --
    confirmed at both the full HTTP level (`oha`, multiple repeated runs)
    and in an isolated multi-threaded microbenchmark with no framework code
    involved at all, so this is a property of many threads holding a
    persistent SQLite connection open concurrently under contention, not a
    bug in this pool's own bookkeeping. `benchmarks/mangoo_app/app.py`
    (WSGI) deliberately leaves this disabled for exactly that reason;
    `benchmarks/mangoo_asgi_app/app.py` (ASGI) deliberately enables it.
    The mechanism is identical in both -- only which concurrency model ends
    up holding several connections open at once under load differs.
    """
    global _pool_enabled
    _pool_enabled = True


def disable_connection_pooling() -> None:
    """Turn pooling back off and close every connection this thread pooled."""
    global _pool_enabled
    _pool_enabled = False
    close_pooled_connections()


def connection_pooling_enabled() -> bool:
    return _pool_enabled


def close_pooled_connections() -> None:
    """Close every connection pooled on the *current* thread.

    Pooling is thread-local, so this only ever affects the calling thread's
    own connections -- there is no cross-thread connection state to close.
    Safe to call whether or not pooling is enabled, and whether or not this
    thread has pooled anything yet.
    """
    pool = getattr(_pool_local, "connections", None)
    if not pool:
        return
    for connection in pool.values():
        try:
            connection.raw.close()
        except Exception:
            pass
    pool.clear()


def _pool_key(config: dict) -> tuple:
    return tuple(sorted((key, str(value)) for key, value in config.items()))


def connect(database: str | Path | dict) -> Connection:
    config = normalize_config(database)
    if _pool_enabled:
        pool = _pool_local.__dict__.setdefault("connections", {})
        key = _pool_key(config)
        cached = pool.get(key)
        if cached is not None:
            return cached
        connection = _open(config)
        connection.pooled = True
        pool[key] = connection
        return connection
    return _open(config)


def _open(config: dict) -> Connection:
    engine = str(config.get("ENGINE", "sqlite3")).lower()
    if engine in {"sqlite", "sqlite3"}:
        raw = sqlite3.connect(config["NAME"])
        raw.row_factory = sqlite3.Row
        return Connection(raw, "sqlite3")
    if engine in {"postgres", "postgresql"}:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise DatabaseDriverError("PostgreSQL requires `uv add psycopg[binary]`.") from error
        raw = psycopg.connect(
            dbname=config["NAME"],
            host=config.get("HOST", "localhost"),
            port=config.get("PORT", 5432),
            user=config.get("USER"),
            password=config.get("PASSWORD"),
            row_factory=dict_row,
        )
        return Connection(raw, "postgresql")
    if engine in {"mysql", "mariadb"}:
        try:
            import pymysql
        except ImportError as error:
            raise DatabaseDriverError("MySQL/MariaDB requires `uv add pymysql`.") from error
        raw = pymysql.connect(
            host=config.get("HOST", "localhost"),
            port=int(config.get("PORT", 3306)),
            user=config.get("USER"),
            password=config.get("PASSWORD"),
            database=config["NAME"],
            cursorclass=pymysql.cursors.DictCursor,
        )
        return Connection(raw, "mysql")
    raise DatabaseConfigurationError(f"Unsupported database engine: {engine}")


def user_id_definition(engine: str) -> str:
    return {"sqlite3": "INTEGER PRIMARY KEY AUTOINCREMENT", "postgresql": "BIGSERIAL PRIMARY KEY", "mysql": "BIGINT AUTO_INCREMENT PRIMARY KEY"}[engine]
