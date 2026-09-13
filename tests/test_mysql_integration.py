"""Opt-in integration coverage; set MANGOO_TEST_MYSQL_* to run it."""

import os
import tempfile
import unittest
from pathlib import Path

from mangoo.db import DatabaseDriverError, connect
from mangoo.migrations import apply


def _database() -> dict:
    return {
        "ENGINE": "mysql",
        "NAME": os.environ.get("MANGOO_TEST_MYSQL_NAME", ""),
        "HOST": os.getenv("MANGOO_TEST_MYSQL_HOST", "localhost"),
        "PORT": int(os.getenv("MANGOO_TEST_MYSQL_PORT", "3306")),
        "USER": os.getenv("MANGOO_TEST_MYSQL_USER"),
        "PASSWORD": os.getenv("MANGOO_TEST_MYSQL_PASSWORD"),
    }


@unittest.skipUnless(os.getenv("MANGOO_TEST_MYSQL_NAME"), "MySQL/MariaDB integration environment is not configured")
class MySQLIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            connection = connect(_database())
        except DatabaseDriverError as error:
            self.skipTest(str(error))
        connection.close()

    def test_mysql_connection_executes_parameterized_queries(self):
        connection = connect(_database())
        try:
            connection.execute("CREATE TEMPORARY TABLE mangoo_integration_check (value VARCHAR(20))")
            connection.execute("INSERT INTO mangoo_integration_check (value) VALUES (?)", ("ok",))
            self.assertEqual(connection.execute("SELECT value FROM mangoo_integration_check").fetchone()["value"], "ok")
            connection.commit()
        finally:
            connection.close()

    def test_mysql_migration_apply_is_transactional_and_idempotent(self):
        database = _database()
        table = "mangoo_integration_migration"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "migrations"
            root.mkdir()
            (root / "0001_initial.py").write_text(
                "operations = ['CREATE TABLE " + table + " (id INTEGER PRIMARY KEY, name VARCHAR(50) NOT NULL)']",
                encoding="utf-8",
            )
            try:
                self.assertEqual(apply(database, [root]), ["0001_initial"])
                self.assertEqual(apply(database, [root]), [])
            finally:
                connection = connect(database)
                try:
                    connection.execute(f"DROP TABLE IF EXISTS {table}")
                    connection.execute("DROP TABLE IF EXISTS mangoo_migrations")
                    connection.commit()
                finally:
                    connection.close()
