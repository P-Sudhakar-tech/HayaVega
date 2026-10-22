import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from mangoo.db import (
    Connection,
    DatabaseConfigurationError,
    SerializedConnection,
    _is_stale_connection_error,
    close_pooled_connections,
    close_pooled_readonly_connections,
    connect,
    connect_readonly,
    connection_pooling_enabled,
    disable_connection_pooling,
    disable_readonly_connection_pooling,
    enable_connection_pooling,
    enable_readonly_connection_pooling,
    readonly_connection_pooling_enabled,
)


class DatabaseTests(unittest.TestCase):
    def test_sqlite_connection_accepts_django_style_config(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = connect({"ENGINE": "sqlite3", "NAME": Path(directory) / "data.sqlite3"})
            try:
                connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
                connection.execute("INSERT INTO sample (name) VALUES (?)", ("Mangoo",))
                connection.commit()
                self.assertEqual(connection.execute("SELECT name FROM sample").fetchone()["name"], "Mangoo")
            finally:
                connection.close()

    def test_unknown_database_engine_is_rejected(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "Unsupported"):
            connect({"ENGINE": "oracle", "NAME": "mangoo"})


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

        with mock.patch("mangoo.db._open", return_value=Connection(fresh_raw, "mysql", config=connection._config)) as opened:
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

        with mock.patch("mangoo.db._open", return_value=Connection(fresh_raw, "mysql", config=connection._config)):
            connection.execute("SELECT 1")

        self.assertIs(connection.raw, fresh_raw)

    def test_non_stale_mysql_error_is_not_retried(self):
        pymysql = self._pymysql()

        syntax_error = pymysql.err.ProgrammingError(1064, "You have an error in your SQL syntax")
        flaky_raw = _FlakyRaw(fail_times=99, exc=syntax_error)
        connection = Connection(flaky_raw, "mysql", config={"ENGINE": "mysql", "NAME": "x"})
        connection.pooled = True

        with mock.patch("mangoo.db._open") as opened:
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

        with mock.patch("mangoo.db._open") as opened:
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

        with mock.patch("mangoo.db._open", return_value=Connection(still_stale_raw, "mysql", config=connection._config)) as opened:
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

    def test_uses_the_native_implementation_when_mangoo_native_is_built(self):
        from mangoo.db import _NativeSerializedConnection

        if _NativeSerializedConnection is None:
            self.skipTest("mangoo_native is not built in this environment")
        serialized = SerializedConnection(self.database)
        self.addCleanup(serialized.close)
        self.assertIsInstance(serialized._impl, _NativeSerializedConnection)

    def test_falls_back_to_pure_python_when_mangoo_native_is_unavailable(self):
        with mock.patch("mangoo.db._NativeSerializedConnection", None):
            serialized = SerializedConnection(self.database)
            self.addCleanup(serialized.close)
            self.assertEqual(type(serialized._impl).__name__, "_FallbackSerializedConnection")
            self.assertEqual(serialized.execute("SELECT id, name FROM sample ORDER BY id"), [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 3, "name": "c"}])
