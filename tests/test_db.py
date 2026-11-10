import importlib.util
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import hayavega.db as db_module
from hayavega.db import (
    Connection,
    DatabaseConfigurationError,
    DatabaseDriverError,
    SerializedConnection,
    _is_stale_connection_error,
    close_pooled_connections,
    close_pooled_readonly_connections,
    connect,
    connect_readonly,
    connection_pooling_enabled,
    disable_connection_pooling,
    disable_readonly_connection_pooling,
    disable_wal_mode,
    enable_connection_pooling,
    enable_readonly_connection_pooling,
    enable_wal_mode,
    readonly_connection_pooling_enabled,
    user_id_definition,
    wal_mode_enabled,
)


class DatabaseTests(unittest.TestCase):
    def test_sqlite_connection_accepts_django_style_config(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = connect({"ENGINE": "sqlite3", "NAME": Path(directory) / "data.sqlite3"})
            try:
                connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
                connection.execute("INSERT INTO sample (name) VALUES (?)", ("Hayavega",))
                connection.commit()
                self.assertEqual(connection.execute("SELECT name FROM sample").fetchone()["name"], "Hayavega")
            finally:
                connection.close()

    def test_unknown_database_engine_is_rejected(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "Unsupported"):
            connect({"ENGINE": "oracle", "NAME": "hayavega"})

    def test_user_id_definition_covers_every_supported_engine(self):
        self.assertEqual(user_id_definition("sqlite3"), "INTEGER PRIMARY KEY AUTOINCREMENT")
        self.assertEqual(user_id_definition("postgresql"), "BIGSERIAL PRIMARY KEY")
        self.assertEqual(user_id_definition("mysql"), "BIGINT AUTO_INCREMENT PRIMARY KEY")


class AtomicTransactionTests(unittest.TestCase):
    """`Connection.atomic()` nests correctly via savepoints -- a nested
    block that raises rolls back to its own savepoint, not the whole
    outer transaction, and a successful nested block releases its
    savepoint without committing (only the outermost block commits)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.connection = connect({"ENGINE": "sqlite3", "NAME": Path(self.directory.name) / "atomic.sqlite3"})
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE rows (id INTEGER PRIMARY KEY, name TEXT)")
        self.connection.commit()

    def test_rollback_delegates_to_the_raw_connection(self):
        self.connection.execute("INSERT INTO rows (name) VALUES ('uncommitted')")
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT COUNT(*) AS n FROM rows").fetchone()["n"], 0)

    def test_a_successful_nested_atomic_block_releases_its_savepoint_not_commits(self):
        with self.connection.atomic():
            self.connection.execute("INSERT INTO rows (name) VALUES ('outer')")
            with self.connection.atomic():
                self.connection.execute("INSERT INTO rows (name) VALUES ('inner')")
        names = sorted(row["name"] for row in self.connection.execute("SELECT name FROM rows").fetchall())
        self.assertEqual(names, ["inner", "outer"])

    def test_a_failing_outer_atomic_block_rolls_back_the_raw_connection(self):
        self.connection.execute("INSERT INTO rows (name) VALUES ('before')")
        self.connection.commit()
        with self.assertRaises(ValueError):
            with self.connection.atomic():
                self.connection.execute("INSERT INTO rows (name) VALUES ('during')")
                raise ValueError("boom")
        names = [row["name"] for row in self.connection.execute("SELECT name FROM rows").fetchall()]
        self.assertEqual(names, ["before"])

    def test_a_failing_nested_atomic_block_rolls_back_only_its_own_savepoint(self):
        with self.connection.atomic():
            self.connection.execute("INSERT INTO rows (name) VALUES ('outer')")
            with self.assertRaises(ValueError):
                with self.connection.atomic():
                    self.connection.execute("INSERT INTO rows (name) VALUES ('inner')")
                    raise ValueError("boom")
        # The inner insert was rolled back to its own savepoint; the outer one still committed.
        names = [row["name"] for row in self.connection.execute("SELECT name FROM rows").fetchall()]
        self.assertEqual(names, ["outer"])


class _RaisingCloseRaw:
    """A fake `.raw` connection whose close() raises -- used to prove
    close_pooled_connections()/close_pooled_readonly_connections() swallow
    a close failure instead of letting it interrupt the sweep.
    sqlite3.Connection itself is an immutable C type and can't be patched
    directly, so a real pooled Connection's `.raw` is swapped for one of
    these instead."""

    def close(self):
        raise RuntimeError("already gone")


class ConnectionPoolingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = {"ENGINE": "sqlite3", "NAME": Path(self.directory.name) / "pool.sqlite3"}
        connect(self.database).close()  # create the file before pooling is ever enabled
        self.addCleanup(disable_connection_pooling)

    def test_pooling_is_disabled_by_default(self):
        self.assertFalse(connection_pooling_enabled())
        first = connect(self.database)
        second = connect(self.database)
        self.assertIsNot(first.raw, second.raw)
        first.close()
        second.close()

    def test_enabled_pooling_reuses_the_same_connection_on_one_thread(self):
        enable_connection_pooling()
        first = connect(self.database)
        first.close()
        second = connect(self.database)
        self.assertIs(first.raw, second.raw)
        second.close()

    def test_pooled_close_does_not_actually_close_the_connection(self):
        enable_connection_pooling()
        connection = connect(self.database)
        connection.close()
        # A real close() would make this raise sqlite3.ProgrammingError.
        connection.execute("SELECT 1").fetchone()

    def test_pooled_close_rolls_back_an_uncommitted_write(self):
        """A connection that outlives a single call must not carry an
        exception's half-finished transaction into whichever request reuses
        it next -- rollback-on-close (not a no-op) is what guarantees that."""
        enable_connection_pooling()
        connection = connect(self.database)
        connection.execute("CREATE TABLE IF NOT EXISTS uncommitted_check (id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.execute("INSERT INTO uncommitted_check (id) VALUES (1)")
        connection.close()  # never committed the insert
        reused = connect(self.database)
        self.assertEqual(reused.execute("SELECT COUNT(*) AS total FROM uncommitted_check").fetchone()["total"], 0)
        reused.close()

    def test_different_database_configs_get_separate_pooled_connections(self):
        enable_connection_pooling()
        other_database = {"ENGINE": "sqlite3", "NAME": Path(self.directory.name) / "other.sqlite3"}
        first = connect(self.database)
        second = connect(other_database)
        self.assertIsNot(first.raw, second.raw)
        first.close()
        second.close()

    def test_pooling_is_thread_local(self):
        enable_connection_pooling()
        main_connection = connect(self.database)
        other_thread_raw = []

        def worker():
            connection = connect(self.database)
            other_thread_raw.append(connection.raw)
            connection.close()
            close_pooled_connections()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertIsNot(main_connection.raw, other_thread_raw[0])
        main_connection.close()

    def test_disable_connection_pooling_closes_pooled_connections(self):
        enable_connection_pooling()
        connection = connect(self.database)
        raw = connection.raw
        connection.close()
        disable_connection_pooling()
        self.assertFalse(connection_pooling_enabled())
        with self.assertRaises(sqlite3.ProgrammingError):
            raw.execute("SELECT 1")

    def test_close_pooled_connections_swallows_a_close_failure_and_still_clears_the_pool(self):
        """A connection whose underlying close() itself raises must not stop
        the rest of the pool from being cleared -- this is a best-effort
        sweep, not a transaction that can partially fail. sqlite3.Connection
        is an immutable C type (can't be patched directly), so swap in a
        fake `.raw` that raises on close() instead."""
        enable_connection_pooling()
        connection = connect(self.database)
        connection.close()
        original_raw = connection.raw
        connection.raw = _RaisingCloseRaw()
        close_pooled_connections()  # must not raise
        original_raw.close()
        # Pool actually cleared despite the raised exception: a fresh connect() must not return the stale object.
        self.assertIsNot(connect(self.database).raw, connection.raw)


class WalModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = {"ENGINE": "sqlite3", "NAME": Path(self.directory.name) / "wal.sqlite3"}
        self.addCleanup(disable_wal_mode)

    def test_disabled_by_default_and_uses_sqlites_own_default_journal_mode(self):
        self.assertFalse(wal_mode_enabled())
        connection = connect(self.database)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            connection.close()

    def test_enabling_sets_wal_and_normal_synchronous_on_new_connections(self):
        enable_wal_mode()
        self.assertTrue(wal_mode_enabled())
        connection = connect(self.database)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 1)  # NORMAL
        finally:
            connection.close()

    def test_a_full_collection_round_trip_still_works_correctly_under_wal(self):
        enable_wal_mode()
        connection = connect(self.database)
        try:
            connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute("INSERT INTO sample (name) VALUES (?)", ("Hayavega",))
            connection.commit()
            self.assertEqual(connection.execute("SELECT name FROM sample").fetchone()["name"], "Hayavega")
        finally:
            connection.close()

    def test_disable_wal_mode_stops_new_connections_from_enabling_it(self):
        enable_wal_mode()
        connect(self.database).close()
        disable_wal_mode()
        self.assertFalse(wal_mode_enabled())
        # The earlier connection already switched this file to WAL --
        # disable_wal_mode() only stops *new* opt-in, it doesn't revert an
        # already-WAL file (that's the documented, disclosed limitation).
        other_database = {"ENGINE": "sqlite3", "NAME": Path(self.directory.name) / "fresh.sqlite3"}
        connection = connect(other_database)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            connection.close()

    def test_non_sqlite_engines_take_the_same_code_path_regardless_of_the_flag(self):
        """enable_wal_mode()'s pragma calls live only in _open()'s sqlite
        branch -- a non-sqlite config must fail exactly the same way
        (DatabaseConfigurationError for an unknown engine) whether or not
        the flag is on, proving the sqlite-only branch guard is real."""
        with self.assertRaises(DatabaseConfigurationError):
            connect({"ENGINE": "oracle", "NAME": "hayavega"})
        enable_wal_mode()
        with self.assertRaises(DatabaseConfigurationError):
            connect({"ENGINE": "oracle", "NAME": "hayavega"})


class ReadonlyFallbackTests(unittest.TestCase):
    """connect_readonly()'s own fallback branches: a non-SQLite engine has
    no read-only mode to speak of, and a read-only open that fails (the
    file doesn't exist yet) falls back to a normal connect() rather than
    raising -- see connect_readonly()'s own docstring."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(disable_readonly_connection_pooling)

    def test_non_sqlite_engine_delegates_straight_to_connect(self):
        config = {"ENGINE": "postgres", "NAME": "hayavega"}
        fake_connection = object()
        with mock.patch("hayavega.db.connect", return_value=fake_connection) as connect_mock:
            result = connect_readonly(config)
        connect_mock.assert_called_once_with(config)
        self.assertIs(result, fake_connection)

    def test_open_readonly_returns_none_for_a_file_that_does_not_exist(self):
        missing = Path(self.directory.name) / "does-not-exist.sqlite3"
        self.assertIsNone(db_module._open_readonly({"NAME": str(missing)}))

    def test_a_missing_file_falls_back_to_a_real_connect_that_creates_it(self):
        missing_path = Path(self.directory.name) / "created-on-fallback.sqlite3"
        database = {"ENGINE": "sqlite3", "NAME": missing_path}
        connection = connect_readonly(database)
        try:
            self.assertTrue(missing_path.exists())
        finally:
            connection.close()

    def test_the_same_fallback_happens_with_readonly_pooling_enabled(self):
        enable_readonly_connection_pooling()
        missing_path = Path(self.directory.name) / "created-on-fallback-pooled.sqlite3"
        database = {"ENGINE": "sqlite3", "NAME": missing_path}
        connection = connect_readonly(database)
        try:
            self.assertTrue(missing_path.exists())
        finally:
            connection.close()


class ReadonlyConnectionPoolingTests(unittest.TestCase):
    """Independent of ConnectionPoolingTests: enable_readonly_connection_pooling()
    only affects connect_readonly(), never connect()."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = {"ENGINE": "sqlite3", "NAME": Path(self.directory.name) / "readonly_pool.sqlite3"}
        connect(self.database).close()  # create the file before pooling is ever enabled
        self.addCleanup(disable_readonly_connection_pooling)

    def test_readonly_pooling_is_disabled_by_default(self):
        self.assertFalse(readonly_connection_pooling_enabled())
        first = connect_readonly(self.database)
        second = connect_readonly(self.database)
        self.assertIsNot(first.raw, second.raw)
        first.close()
        second.close()

    def test_enabled_readonly_pooling_reuses_the_same_connection_on_one_thread(self):
        enable_readonly_connection_pooling()
        first = connect_readonly(self.database)
        first.close()
        second = connect_readonly(self.database)
        self.assertIs(first.raw, second.raw)
        second.close()

    def test_readonly_pooling_does_not_enable_write_pooling(self):
        enable_readonly_connection_pooling()
        self.assertFalse(connection_pooling_enabled())
        first = connect(self.database)
        second = connect(self.database)
        self.assertIsNot(first.raw, second.raw)
        first.close()
        second.close()

    def test_pooling_is_thread_local(self):
        enable_readonly_connection_pooling()
        main_connection = connect_readonly(self.database)
        other_thread_raw = []

        def worker():
            connection = connect_readonly(self.database)
            other_thread_raw.append(connection.raw)
            connection.close()
            close_pooled_readonly_connections()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertIsNot(main_connection.raw, other_thread_raw[0])
        main_connection.close()

    def test_disable_readonly_connection_pooling_closes_pooled_connections(self):
        enable_readonly_connection_pooling()
        connection = connect_readonly(self.database)
        raw = connection.raw
        connection.close()
        disable_readonly_connection_pooling()
        self.assertFalse(readonly_connection_pooling_enabled())
        with self.assertRaises(sqlite3.ProgrammingError):
            raw.execute("SELECT 1")

    def test_close_pooled_readonly_connections_swallows_a_close_failure_and_still_clears_the_pool(self):
        enable_readonly_connection_pooling()
        connection = connect_readonly(self.database)
        connection.close()
        original_raw = connection.raw
        connection.raw = _RaisingCloseRaw()
        close_pooled_readonly_connections()  # must not raise
        original_raw.close()
        self.assertIsNot(connect_readonly(self.database).raw, connection.raw)


class _FlakyCursor:
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params):
        self._raw.calls += 1
        if self._raw.calls <= self._raw.fail_times:
            raise self._raw.exc

    def fetchone(self):
        return {"ok": True}


class _FlakyRaw:
    """A fake DB-API connection whose `cursor().execute()` raises `exc` the
    first `fail_times` calls, then succeeds -- simulates a pooled connection
    that the server has silently dropped."""

    def __init__(self, fail_times: int, exc: Exception):
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

    def cursor(self):
        return _FlakyCursor(self)


class StaleConnectionReconnectTests(unittest.TestCase):
    """`Connection.execute()`'s reactive reconnect for pooled MySQL/PostgreSQL
    connections (see `_reconnect`/`_is_stale_connection_error` in db.py) --
    the "known limitation" `enable_connection_pooling()` used to document as
    unsolved. Exercised against fake raw connections so this doesn't need a
    real, droppable MySQL/PostgreSQL server -- only the driver package, for
    its exception types (pymysql is a dev dependency for exactly this reason;
    psycopg is not, so its one test skips without it)."""

    def _pymysql(self):
        try:
            import pymysql
        except ImportError:
            self.skipTest("pymysql is not installed")
        return pymysql

    def test_stale_mysql_connection_is_reconnected_and_retried(self):
        pymysql = self._pymysql()

        stale = pymysql.err.OperationalError(2006, "MySQL server has gone away")
        flaky_raw = _FlakyRaw(fail_times=1, exc=stale)
        connection = Connection(flaky_raw, "mysql", config={"ENGINE": "mysql", "NAME": "x"})
        connection.pooled = True
        fresh_raw = _FlakyRaw(fail_times=0, exc=stale)

        with mock.patch("hayavega.db._open", return_value=Connection(fresh_raw, "mysql", config=connection._config)) as opened:
            result = connection.execute("SELECT 1").fetchone()

        opened.assert_called_once_with(connection._config)
        self.assertIs(connection.raw, fresh_raw)
        self.assertEqual(result, {"ok": True})

    def test_mysql_interface_error_is_also_treated_as_stale(self):
        pymysql = self._pymysql()

        stale = pymysql.err.InterfaceError(0, "")
        flaky_raw = _FlakyRaw(fail_times=1, exc=stale)
        connection = Connection(flaky_raw, "mysql", config={"ENGINE": "mysql", "NAME": "x"})
        connection.pooled = True
        fresh_raw = _FlakyRaw(fail_times=0, exc=stale)

        with mock.patch("hayavega.db._open", return_value=Connection(fresh_raw, "mysql", config=connection._config)):
            connection.execute("SELECT 1")

        self.assertIs(connection.raw, fresh_raw)

    def test_non_stale_mysql_error_is_not_retried(self):
        pymysql = self._pymysql()

        syntax_error = pymysql.err.ProgrammingError(1064, "You have an error in your SQL syntax")
        flaky_raw = _FlakyRaw(fail_times=99, exc=syntax_error)
        connection = Connection(flaky_raw, "mysql", config={"ENGINE": "mysql", "NAME": "x"})
        connection.pooled = True

        with mock.patch("hayavega.db._open") as opened:
            with self.assertRaises(pymysql.err.ProgrammingError):
                connection.execute("SELECT 1")

        opened.assert_not_called()
        self.assertIs(connection.raw, flaky_raw)

    def test_unpooled_connection_is_never_reconnected(self):
        pymysql = self._pymysql()

        stale = pymysql.err.OperationalError(2006, "MySQL server has gone away")
        flaky_raw = _FlakyRaw(fail_times=99, exc=stale)
        connection = Connection(flaky_raw, "mysql", config={"ENGINE": "mysql", "NAME": "x"})
        connection.pooled = False

        with mock.patch("hayavega.db._open") as opened:
            with self.assertRaises(pymysql.err.OperationalError):
                connection.execute("SELECT 1")

        opened.assert_not_called()

    def test_a_second_consecutive_stale_failure_still_raises(self):
        """The retry happens exactly once -- a connection that comes back
        stale again is a real error, not an infinite retry loop."""
        pymysql = self._pymysql()

        stale = pymysql.err.OperationalError(2006, "MySQL server has gone away")
        flaky_raw = _FlakyRaw(fail_times=99, exc=stale)
        connection = Connection(flaky_raw, "mysql", config={"ENGINE": "mysql", "NAME": "x"})
        connection.pooled = True
        still_stale_raw = _FlakyRaw(fail_times=99, exc=stale)

        with mock.patch("hayavega.db._open", return_value=Connection(still_stale_raw, "mysql", config=connection._config)) as opened:
            with self.assertRaises(pymysql.err.OperationalError):
                connection.execute("SELECT 1")

        opened.assert_called_once()

    def test_is_stale_connection_error_classifies_mysql_error_codes(self):
        pymysql = self._pymysql()

        self.assertTrue(_is_stale_connection_error("mysql", pymysql.err.OperationalError(2006, "gone away")))
        self.assertTrue(_is_stale_connection_error("mysql", pymysql.err.OperationalError(2013, "lost connection")))
        self.assertTrue(_is_stale_connection_error("mysql", pymysql.err.InterfaceError(0, "")))
        self.assertFalse(_is_stale_connection_error("mysql", pymysql.err.OperationalError(1045, "access denied")))
        self.assertFalse(_is_stale_connection_error("mysql", pymysql.err.ProgrammingError(1064, "syntax error")))

    def test_is_stale_connection_error_classifies_postgresql_operational_errors(self):
        try:
            import psycopg
        except ImportError:
            self.skipTest("psycopg is not installed")
        self.assertTrue(_is_stale_connection_error("postgresql", psycopg.OperationalError("server closed the connection unexpectedly")))
        self.assertFalse(_is_stale_connection_error("postgresql", psycopg.errors.UniqueViolation("duplicate key")))

    def test_sqlite_is_never_eligible_for_reconnect(self):
        self.assertFalse(_is_stale_connection_error("sqlite3", RuntimeError("anything")))


class SerializedConnectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "data.sqlite3"
        connection = connect(self.database)
        try:
            connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute("INSERT INTO sample (name) VALUES (?), (?), (?)", ("a", "b", "c"))
            connection.commit()
        finally:
            connection.close()

    def test_execute_returns_materialized_dicts_matching_connect_readonly(self):
        reference = connect_readonly(self.database)
        try:
            expected = [dict(row) for row in reference.execute("SELECT id, name FROM sample ORDER BY id").fetchall()]
        finally:
            reference.close()
        serialized = SerializedConnection(self.database)
        self.addCleanup(serialized.close)
        self.assertEqual(serialized.execute("SELECT id, name FROM sample ORDER BY id"), expected)

    def test_execute_accepts_bind_parameters(self):
        serialized = SerializedConnection(self.database)
        self.addCleanup(serialized.close)
        self.assertEqual(serialized.execute("SELECT id, name FROM sample WHERE name = ?", ("b",)), [{"id": 2, "name": "b"}])

    def test_non_sqlite_engine_is_rejected(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "sqlite3"):
            SerializedConnection({"ENGINE": "postgresql", "NAME": "x"})

    def test_close_then_execute_raises_rather_than_hangs(self):
        serialized = SerializedConnection(self.database)
        serialized.close()
        with self.assertRaises(RuntimeError):
            serialized.execute("SELECT 1")

    def test_many_concurrent_callers_all_get_correct_results(self):
        """The one correctness property this type's whole design depends
        on: every caller thread gets back exactly its own query's result,
        never another thread's, however many are submitted concurrently."""
        serialized = SerializedConnection(self.database)
        self.addCleanup(serialized.close)
        errors = []

        def worker(expected_id: int):
            try:
                for _ in range(20):
                    rows = serialized.execute("SELECT id, name FROM sample WHERE id = ?", (expected_id,))
                    if rows != [{"id": expected_id, "name": {1: "a", 2: "b", 3: "c"}[expected_id]}]:
                        errors.append((expected_id, rows))
            except Exception as exc:  # noqa: BLE001 -- collected and asserted on below
                errors.append((expected_id, exc))

        threads = [threading.Thread(target=worker, args=(expected_id,)) for expected_id in (1, 2, 3) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads), "a worker thread never completed -- possible deadlock")
        self.assertEqual(errors, [])

    def test_uses_the_native_implementation_when_hayavega_native_is_built(self):
        from hayavega.db import _NativeSerializedConnection

        if _NativeSerializedConnection is None:
            self.skipTest("hayavega_native is not built in this environment")
        serialized = SerializedConnection(self.database)
        self.addCleanup(serialized.close)
        self.assertIsInstance(serialized._impl, _NativeSerializedConnection)

    def test_falls_back_to_pure_python_when_hayavega_native_is_unavailable(self):
        with mock.patch("hayavega.db._NativeSerializedConnection", None):
            serialized = SerializedConnection(self.database)
            self.addCleanup(serialized.close)
            self.assertEqual(type(serialized._impl).__name__, "_FallbackSerializedConnection")
            self.assertEqual(serialized.execute("SELECT id, name FROM sample ORDER BY id"), [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 3, "name": "c"}])

    def test_fallback_close_then_execute_raises_rather_than_hangs(self):
        """Same contract as the native path's own close-then-execute test
        above, but forced onto _FallbackSerializedConnection specifically
        -- the native implementation is what SerializedConnection actually
        uses in this environment, so without forcing the fallback here,
        this specific implementation's own closed-check never runs."""
        with mock.patch("hayavega.db._NativeSerializedConnection", None):
            serialized = SerializedConnection(self.database)
            serialized.close()
            with self.assertRaises(RuntimeError):
                serialized.execute("SELECT 1")


class NativeSerializedConnectionImportFallbackTests(unittest.TestCase):
    """hayavega.db's `try: import hayavega_native ... except ImportError:
    _NativeSerializedConnection = None` only runs at module import time.
    `test_falls_back_to_pure_python_when_hayavega_native_is_unavailable` above
    mocks the already-bound attribute, which never executes those two lines.

    Reaching them for real means re-executing the module with `hayavega_native`
    made unimportable -- but `importlib.reload(hayavega.db)` would do that *in
    place*, redefining every class in the live module (including
    `DatabaseDriverError`, `Connection`, ...) as a brand-new class object.
    Every other test in this process that imported those names before the
    reload would then hold a stale class, e.g. making `assertRaises
    (DatabaseDriverError, ...)` fail even when the right error is raised,
    since it's no longer the *same* class the module now raises. So instead
    of reloading the shared module, a throwaway module object is loaded from
    the same source file under a different name -- `hayavega.db` itself, and
    everything already imported from it, is never touched."""

    def test_module_level_import_falls_back_when_hayavega_native_is_unavailable(self):
        original = sys.modules.get("hayavega_native", None)
        sys.modules["hayavega_native"] = None  # sentinel: forces ImportError on import
        try:
            spec = importlib.util.spec_from_file_location("hayavega._db_reimport_test", db_module.__file__)
            fresh_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fresh_module)
            self.assertIsNone(fresh_module._NativeSerializedConnection)
        finally:
            if original is None:
                sys.modules.pop("hayavega_native", None)
            else:
                sys.modules["hayavega_native"] = original


class DriverImportFallbackTests(unittest.TestCase):
    """`_open()`'s postgres/mysql branches import their driver lazily (inside
    the function, not at module load) and raise `DatabaseDriverError` if it
    isn't installed -- so, unlike the `hayavega_native` cases above, forcing
    this only needs `sys.modules` patched for the one call, no reload."""

    def _force_missing(self, module_name):
        original = sys.modules.get(module_name, None)
        sys.modules[module_name] = None  # sentinel: forces ImportError on import
        return module_name, original

    def _restore(self, forced):
        module_name, original = forced
        if original is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original

    def test_postgres_driver_missing_raises_an_actionable_error(self):
        forced = self._force_missing("psycopg")
        try:
            with self.assertRaisesRegex(DatabaseDriverError, "psycopg"):
                connect({"ENGINE": "postgresql", "NAME": "hayavega"})
        finally:
            self._restore(forced)

    def test_mysql_driver_missing_raises_an_actionable_error(self):
        forced = self._force_missing("pymysql")
        try:
            with self.assertRaisesRegex(DatabaseDriverError, "pymysql"):
                connect({"ENGINE": "mysql", "NAME": "hayavega"})
        finally:
            self._restore(forced)


class DriverConnectCallTests(unittest.TestCase):
    """`_open()`'s postgres/mysql branches, exercised against the real driver
    packages with their own `.connect()` mocked out -- this proves `_open()`
    calls the driver correctly and wraps the result in `Connection`, without
    needing a real PostgreSQL/MySQL server (that's covered separately by the
    opt-in integration tests)."""

    def test_open_postgres_calls_psycopg_connect_with_the_given_config_and_wraps_it(self):
        try:
            import psycopg
        except ImportError:
            self.skipTest("psycopg is not installed")
        fake_raw = mock.Mock()
        with mock.patch.object(psycopg, "connect", return_value=fake_raw) as connect_mock:
            connection = connect({"ENGINE": "postgresql", "NAME": "mydb", "HOST": "dbhost", "PORT": 6543, "USER": "alice", "PASSWORD": "s3cret"})
        connect_mock.assert_called_once()
        kwargs = connect_mock.call_args.kwargs
        self.assertEqual(kwargs["dbname"], "mydb")
        self.assertEqual(kwargs["host"], "dbhost")
        self.assertEqual(kwargs["port"], 6543)
        self.assertEqual(kwargs["user"], "alice")
        self.assertEqual(kwargs["password"], "s3cret")
        self.assertIs(connection.raw, fake_raw)
        self.assertEqual(connection.engine, "postgresql")

    def test_open_mysql_calls_pymysql_connect_with_the_given_config_and_wraps_it(self):
        try:
            import pymysql
        except ImportError:
            self.skipTest("pymysql is not installed")
        fake_raw = mock.Mock()
        with mock.patch.object(pymysql, "connect", return_value=fake_raw) as connect_mock:
            connection = connect({"ENGINE": "mysql", "NAME": "mydb", "HOST": "dbhost", "PORT": 3307, "USER": "alice", "PASSWORD": "s3cret"})
        connect_mock.assert_called_once()
        kwargs = connect_mock.call_args.kwargs
        self.assertEqual(kwargs["host"], "dbhost")
        self.assertEqual(kwargs["port"], 3307)
        self.assertEqual(kwargs["database"], "mydb")
        self.assertEqual(kwargs["user"], "alice")
        self.assertEqual(kwargs["password"], "s3cret")
        self.assertIs(connection.raw, fake_raw)
        self.assertEqual(connection.engine, "mysql")
