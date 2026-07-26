import tempfile
import unittest
from pathlib import Path

from mangoo.db import DatabaseConfigurationError, connect


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
