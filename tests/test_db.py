import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from mangoo.db import (
    DatabaseConfigurationError,
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
