"""Opt-in integration coverage; set HAYAVEGA_TEST_POSTGRES_* to run it."""

import os
import unittest

from hayavega.db import DatabaseDriverError, connect


@unittest.skipUnless(os.getenv("HAYAVEGA_TEST_POSTGRES_NAME"), "PostgreSQL integration environment is not configured")
class PostgreSQLIntegrationTests(unittest.TestCase):
    def test_postgresql_connection_executes_parameterized_queries(self):
        database = {
            "ENGINE": "postgresql",
            "NAME": os.environ["HAYAVEGA_TEST_POSTGRES_NAME"],
            "HOST": os.getenv("HAYAVEGA_TEST_POSTGRES_HOST", "localhost"),
            "PORT": int(os.getenv("HAYAVEGA_TEST_POSTGRES_PORT", "5432")),
            "USER": os.getenv("HAYAVEGA_TEST_POSTGRES_USER"),
            "PASSWORD": os.getenv("HAYAVEGA_TEST_POSTGRES_PASSWORD"),
        }
        try:
            connection = connect(database)
        except DatabaseDriverError as error:
            self.skipTest(str(error))
        try:
            connection.execute("CREATE TEMP TABLE hayavega_integration_check (value TEXT)")
            connection.execute("INSERT INTO hayavega_integration_check (value) VALUES (?)", ("ok",))
            self.assertEqual(connection.execute("SELECT value FROM hayavega_integration_check").fetchone()["value"], "ok")
            connection.commit()
        finally:
            connection.close()
