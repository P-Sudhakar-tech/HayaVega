"""Pluggable DB-API database connections for Mangoo."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    from mangoo_native import SerializedConnection as _NativeSerializedConnection
except ImportError:
    _NativeSerializedConnection = None


class DatabaseConfigurationError(ValueError):
    pass


class DatabaseDriverError(RuntimeError):
    pass


def normalize_config(database: str | Path | dict) -> dict:
    if isinstance(database, (str, Path)):
        return {"ENGINE": "sqlite3", "NAME": str(database)}
    return dict(database)


class Connection:
    def __init__(self, raw, engine: str, config: dict | None = None):
        self.raw, self.engine = raw, engine
        self._config = config
        self._transaction_depth = 0
        self.pooled = False

    def execute(self, sql: str, params=()):
        """Run one statement, transparently reconnecting a stale pooled connection once.

        Only pooled MySQL/PostgreSQL connections are eligible: a pooled
        connection can sit idle long enough for the server to drop it
        (MySQL's `wait_timeout`, a network blip) between requests, and the
        *first* statement run against it afterwards is what surfaces that --
        there is no local, network-free way to detect it beforehand. Since
        the failure happens before anything reaches the server (the driver
        can't even write the request on a dead socket), reopening the
        connection and retrying the exact same statement once is safe: it
        cannot double-execute a statement the server never saw. A second
        failure after reconnecting is a real error and propagates normally.
        SQLite is never eligible -- it has no server-side idle timeout, so
        this path can't help it and the check is skipped entirely.
        """
        try:
            return self._execute_once(sql, params)
        except Exception as exc:
            if self.pooled and self.engine in {"postgresql", "mysql"} and _is_stale_connection_error(self.engine, exc):
                self._reconnect()
                return self._execute_once(sql, params)
            raise

    def _execute_once(self, sql: str, params=()):
        cursor = self.raw.cursor()
        cursor.execute(sql.replace("?", "%s") if self.engine in {"postgresql", "mysql"} else sql, params)
        return cursor

    def _reconnect(self) -> None:
        assert self._config is not None, "reconnect is only reachable for connections opened via _open(), which always sets _config"
        fresh = _open(self._config)
        self.raw = fresh.raw

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
    or one-coroutine-at-a-time-per-thread (ASGI sync views run on
    `create_asgi_application`'s dedicated `max_sync_workers`-sized thread
    pool) model this framework already uses, rather than a general bounded
    pool shared across threads with checkout/checkin
    queuing. That keeps the mechanism simple and avoids the cross-thread
    connection-sharing hazards DB-API connections generally aren't safe for
    (SQLite enforces this directly: a connection created with the default
    `check_same_thread=True` cannot be used from another thread at all).

    A pooled MySQL/PostgreSQL connection that has sat idle long enough for
    the server to drop it (MySQL's `wait_timeout`, a network blip) is
    transparently reconnected: `Connection.execute()` recognizes the
    driver-specific "connection is dead" error, reopens the underlying
    connection, and retries the same statement once -- see
    `_is_stale_connection_error`/`Connection._reconnect`. This does not
    affect SQLite, which has no server-side idle timeout and is never
    eligible for this path.

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


_readonly_pool_enabled = False
_readonly_pool_local = threading.local()


def enable_readonly_connection_pooling() -> None:
    """Reuse one read-only connection per (thread, database config) instead
    of opening a fresh one on every `connect_readonly()` call.

    Independent of `enable_connection_pooling()` -- that function pools
    read-write connections and, measured against this project's own
    `benchmarks/`, regresses the WSGI path by ~10-15% (many threads holding
    a *read-write* connection open contends under SQLite on this
    environment; see its own docstring for the full result). A read-only
    connection takes no reserved/pending locks at all, so holding one open
    per thread was hypothesized not to reproduce that contention -- and
    an isolated 16-thread microbenchmark confirmed the hypothesis at that
    layer: pooling read-only connections beat both a fresh read-only
    connection per call and a pooled *read-write* connection (which
    reproduced the known regression, as a same-run sanity check) by
    ~27-43% across two runs. Only affects `connect_readonly()` -- the write
    path (`connect()`, and therefore `Collection.append()`/
    `Selection.revise()`/`erase()`) is completely unaffected by this flag
    and stays governed by `enable_connection_pooling()` alone.

    **That isolated win did not survive to the HTTP level -- measured, not
    assumed.** Four paired `oha -z 10s -c 30` runs against
    `benchmarks/mangoo_app/app.py` under real `waitress`, with and without
    this enabled: `db_list` averaged 811 req/s pooled vs. 840 req/s
    unpooled; `template_render` averaged 708 req/s pooled vs. 735 req/s
    unpooled -- both within this scenario's established run-to-run noise
    band (up to ~17%, see `INDUSTRY_LEADERSHIP_ROADMAP.md`'s
    `template_render` re-measurement), so neither a real win nor a real
    loss, not the substantial win the isolated microbenchmark predicted.
    `benchmarks/mangoo_app/app.py` (WSGI) deliberately leaves this
    disabled for exactly that reason: the sub-millisecond connection-layer
    saving this recovers is proportionally invisible against a request
    whose own `sqlite3.connect()`+`.execute()` already costs ~35-45ms
    end to end on this environment -- the same "environment ceiling"
    finding every other connection-layer optimization in this document has
    run into. Kept available and tested (not reverted) since it is a real,
    correct capability that may still help a different deployment's
    workload shape; just not proven to help this one.

    **Benchmark your own deployment before enabling -- disclosed exactly
    like every other tunable in this module.** A pooled read-only SQLite
    connection reads a point-in-time snapshot of the schema and any
    long-lived cached state the driver keeps; this framework's own
    `Collection.prepare()` cache already assumes a table's schema never
    changes without a migration, so this carries no additional risk beyond
    what `enable_connection_pooling()` already documents for MySQL/
    PostgreSQL connections going stale after a long idle period.
    """
    global _readonly_pool_enabled
    _readonly_pool_enabled = True


def disable_readonly_connection_pooling() -> None:
    """Turn read-only pooling back off and close every connection this thread pooled."""
    global _readonly_pool_enabled
    _readonly_pool_enabled = False
    close_pooled_readonly_connections()


def readonly_connection_pooling_enabled() -> bool:
    return _readonly_pool_enabled


def close_pooled_readonly_connections() -> None:
    """Close every read-only connection pooled on the *current* thread.

    Mirrors `close_pooled_connections()`, kept as a separate pool so
    enabling/disabling read-only pooling never touches the write-path pool.
    """
    pool = getattr(_readonly_pool_local, "connections", None)
    if not pool:
        return
    for connection in pool.values():
        try:
            connection.raw.close()
        except Exception:
            pass
    pool.clear()


def connect_readonly(database: str | Path | dict) -> Connection:
    """Open a connection scoped to reads only.

    For SQLite, this opens the file with the `mode=ro` URI parameter instead
    of a plain read-write connection. Measured (`benchmarks/
    sqlite_readonly_reads.py`) at a real, consistent win on this project's
    actual default (`journal_mode=DELETE`, untouched): ~25-30% faster per
    call single-threaded and ~18-30% higher aggregate throughput under
    16-thread contention, on Windows -- a read-only open skips the
    reserved/pending-lock bookkeeping SQLite otherwise does on every
    statement to guard against a writer, which a connection that will only
    ever `SELECT` never needs.

    Never pooled by default -- opened and closed fresh on every call, which
    does not reproduce the many-threads-holding-a-connection-open
    contention that made pooling regress the WSGI path under
    `enable_connection_pooling()`. Pass through `enable_readonly_connection_pooling()`
    to additionally reuse one read-only connection per thread; see that
    function's docstring for why pooling *read-only* connections was found
    not to carry the write-pooling regression.

    Only used internally by `Selection.fetch()`/`values()`/`total()` --
    pure-read call sites that never call `.commit()`. `Collection.append()`/
    `Selection.revise()`/`erase()` are unaffected and keep using `connect()`.

    Falls back to a normal `connect()` if the read-only open fails (for
    example, the database file does not exist yet) rather than raising --
    in practice this never happens on the read path, since every read call
    site calls `Collection.prepare()` first, which always creates the file
    via a normal read-write `connect()` if it is missing.

    Non-SQLite engines have no equivalent parameter tested here and fall
    back to `connect()` unchanged.
    """
    config = normalize_config(database)
    engine = str(config.get("ENGINE", "sqlite3")).lower()
    if engine not in {"sqlite", "sqlite3"}:
        return connect(database)
    if _readonly_pool_enabled:
        pool = _readonly_pool_local.__dict__.setdefault("connections", {})
        key = _pool_key(config)
        cached = pool.get(key)
        if cached is not None:
            return cached
        connection = _open_readonly(config)
        if connection is None:
            return connect(database)
        connection.pooled = True
        pool[key] = connection
        return connection
    connection = _open_readonly(config)
    return connection if connection is not None else connect(database)


def _open_readonly(config: dict) -> Connection | None:
    try:
        raw = sqlite3.connect(f"file:{config['NAME']}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    raw.row_factory = sqlite3.Row
    return Connection(raw, "sqlite3")


class _FallbackSerializedConnection:
    """Pure-Python equivalent of `mangoo_native.SerializedConnection` --
    one read-only connection, one lock serializing every caller's access
    to it, used automatically when `mangoo_native` isn't built. Slower
    than the Rust version (a real OS lock, not a dedicated worker thread
    and channel), but architecturally the same idea: one connection,
    never contended for concurrently."""

    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row

    def execute(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            if self._connection is None:
                raise RuntimeError("SerializedConnection is closed")
            return [dict(row) for row in self._connection.execute(sql, params).fetchall()]

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None


class SerializedConnection:
    """One read-only SQLite connection, shared across every caller via
    serialized access instead of a connection per thread -- an opt-in
    alternative to `connect_readonly()`/`enable_readonly_connection_pooling()`
    for a hot, read-heavy path, backed by `mangoo_native.SerializedConnection`
    (a dedicated Rust worker thread) when built, or `_FallbackSerializedConnection`
    (a Python lock) otherwise -- same interface either way, no configuration
    required to get the faster path when it's available.

    **Measured, disclosed trade-off, not a universal win** -- benchmark your
    own deployment before reaching for this, the same way
    `enable_connection_pooling()`'s own docstring already asks. Measured
    (`benchmarks/serialized_connection_vs_python.py`) at a real, consistent
    ~1.5-1.7x throughput improvement over both the unpooled default and
    `enable_readonly_connection_pooling()` for a `SELECT ... LIMIT ? OFFSET ?`-
    shaped read under WSGI/waitress contention -- but every query now queues
    behind whichever one is currently running, trading SQLite's own
    per-connection lock contention for a single serialization point. A
    read pattern with occasional genuinely slow queries could see those
    queue up behind each other in a way N independent connections wouldn't.

    SQLite only; read-only only (no `.commit()`/write support) -- use
    `connect()`/`enable_connection_pooling()` for writes. Not currently
    integrated into `Selection`/`Entity` -- call `.execute(sql, params)`
    directly with raw SQL and get back a `list[dict]`, already materialized
    (not a cursor, unlike every other `Connection` in this module).
    """

    def __init__(self, database: str | Path | dict):
        config = normalize_config(database)
        engine = str(config.get("ENGINE", "sqlite3")).lower()
        if engine not in {"sqlite", "sqlite3"}:
            raise DatabaseConfigurationError(f"SerializedConnection only supports sqlite3 -- got {engine!r}.")
        path = str(config["NAME"])
        self._impl = _NativeSerializedConnection(path) if _NativeSerializedConnection is not None else _FallbackSerializedConnection(path)

    def execute(self, sql: str, params=()) -> list[dict]:
        return self._impl.execute(sql, params)

    def close(self) -> None:
        self._impl.close()


def _open(config: dict) -> Connection:
    engine = str(config.get("ENGINE", "sqlite3")).lower()
    if engine in {"sqlite", "sqlite3"}:
        raw = sqlite3.connect(config["NAME"])
        raw.row_factory = sqlite3.Row
        return Connection(raw, "sqlite3", config)
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
        return Connection(raw, "postgresql", config)
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
        return Connection(raw, "mysql", config)
    raise DatabaseConfigurationError(f"Unsupported database engine: {engine}")


def _is_stale_connection_error(engine: str, exc: Exception) -> bool:
    """Whether `exc` indicates the server dropped a connection out from under us.

    Distinguishes "connection is dead, retry on a fresh one" from ordinary
    query errors (bad SQL, constraint violations) that a retry would just
    reproduce -- only the former should trigger `Connection._reconnect()`.
    """
    if engine == "mysql":
        import pymysql

        if isinstance(exc, pymysql.err.InterfaceError):
            return True
        if isinstance(exc, pymysql.err.OperationalError):
            code = exc.args[0] if exc.args else None
            # 2006 server gone away, 2013 lost connection during query,
            # 2003/2014 can't connect/out-of-sync after a dropped socket.
            return code in {2003, 2006, 2013, 2014}
        return False
    if engine == "postgresql":
        import psycopg

        return isinstance(exc, psycopg.OperationalError)
    return False


def user_id_definition(engine: str) -> str:
    return {"sqlite3": "INTEGER PRIMARY KEY AUTOINCREMENT", "postgresql": "BIGSERIAL PRIMARY KEY", "mysql": "BIGINT AUTO_INCREMENT PRIMARY KEY"}[engine]
