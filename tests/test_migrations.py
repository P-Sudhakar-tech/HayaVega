import tempfile
import unittest
from pathlib import Path
from types import ModuleType

from mangoo.db import connect
from mangoo.migrations import MigrationError, MigrationGenerationError, MigrationIntegrityError, apply, generate, status
from mangoo.models import Entity, Integer, Text


class MigrationTests(unittest.TestCase):
    def _migration(self, directory, name, operations):
        (directory / f"{name}.py").write_text("operations = " + repr(operations), encoding="utf-8")

    def test_apply_is_idempotent_and_status_marks_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root, database = Path(temp) / "migrations", Path(temp) / "data.sqlite3"
            root.mkdir()
            self._migration(root, "0001_products", ["CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"])
            self.assertEqual(apply(database, [root]), ["0001_products"])
            self.assertEqual(apply(database, [root]), [])
            self.assertEqual(status(database, [root])[0].applied, True)

    def test_failed_migration_rolls_back_schema_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root, database = Path(temp) / "migrations", Path(temp) / "data.sqlite3"
            root.mkdir()
            self._migration(root, "0001_broken", ["CREATE TABLE transient_data (id INTEGER)", "INSERT INTO missing_table VALUES (1)"])
            with self.assertRaises(MigrationError):
                apply(database, [root])
            connection = connect(database)
            try:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", ("transient_data",)).fetchall()
                self.assertEqual(tables, [])
                self.assertEqual(connection.execute("SELECT COUNT(*) AS total FROM mangoo_migrations").fetchone()["total"], 0)
            finally:
                connection.close()

    def test_altered_or_missing_applied_migration_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root, database = Path(temp) / "migrations", Path(temp) / "data.sqlite3"
            root.mkdir()
            self._migration(root, "0001_products", ["CREATE TABLE products (id INTEGER PRIMARY KEY)"])
            apply(database, [root])
            self._migration(root, "0001_products", ["CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"])
            with self.assertRaises(MigrationIntegrityError):
                apply(database, [root])
            (root / "0001_products.py").unlink()
            with self.assertRaises(MigrationIntegrityError):
                status(database, [root])

    def test_generator_creates_deterministic_initial_entity_migration(self):
        module = ModuleType("catalog.models")
        Product = type("Product", (Entity,), {"__module__": module.__name__, "name": Text(unique=True), "stock": Integer(default=0)})
        module.Product = Product
        with tempfile.TemporaryDirectory() as temp:
            source = generate(module, Path(temp) / "migrations")
            self.assertEqual(source.name, "0001_initial.py")
            self.assertIn("CREATE TABLE product_entries", source.read_text(encoding="utf-8"))
            self.assertIn("name TEXT NOT NULL UNIQUE", source.read_text(encoding="utf-8"))
            with self.assertRaises(MigrationGenerationError):
                generate(module, Path(temp) / "migrations")
