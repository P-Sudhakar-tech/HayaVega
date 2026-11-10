import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from hayavega.db import connect
from hayavega.migrations import MigrationError, MigrationFormatError, MigrationGenerationError, MigrationIntegrityError, _identifier, apply, discover, generate, migration_directories, status
from hayavega.models import Decimal, Entity, Integer, Text


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
                self.assertEqual(connection.execute("SELECT COUNT(*) AS total FROM hayavega_migrations").fetchone()["total"], 0)
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
            self.assertIsNone(generate(module, Path(temp) / "migrations"))

    def test_generator_creates_only_safe_nullable_column_additions(self):
        first = ModuleType("catalog.models")
        first.Product = type("Product", (Entity,), {"__module__": first.__name__, "name": Text()})
        second = ModuleType("catalog.models")
        second.Product = type("Product", (Entity,), {"__module__": second.__name__, "name": Text(), "summary": Text(nullable=True)})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "migrations"
            generate(first, root)
            source = generate(second, root)
            self.assertEqual(source.name, "0002_auto.py")
            self.assertIn("dependencies = ['0001_initial']", source.read_text(encoding="utf-8"))
            self.assertIn("ALTER TABLE product_entries ADD COLUMN summary TEXT", source.read_text(encoding="utf-8"))
            self.assertIsNone(generate(second, root))

    def test_generator_rejects_removed_columns(self):
        first = ModuleType("catalog.models")
        first.Product = type("Product", (Entity,), {"__module__": first.__name__, "name": Text()})
        removed = ModuleType("catalog.models")
        removed.Product = type("Product", (Entity,), {"__module__": removed.__name__})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "migrations"
            generate(first, root)
            with self.assertRaises(MigrationGenerationError):
                generate(removed, root)

    def test_generator_rejects_column_type_alteration(self):
        first = ModuleType("catalog.models")
        first.Product = type("Product", (Entity,), {"__module__": first.__name__, "price": Decimal()})
        altered = ModuleType("catalog.models")
        altered.Product = type("Product", (Entity,), {"__module__": altered.__name__, "price": Text()})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "migrations"
            generate(first, root)
            with self.assertRaises(MigrationGenerationError):
                generate(altered, root)

    def test_generator_rejects_uniqueness_alteration(self):
        first = ModuleType("catalog.models")
        first.Product = type("Product", (Entity,), {"__module__": first.__name__, "name": Text()})
        altered = ModuleType("catalog.models")
        altered.Product = type("Product", (Entity,), {"__module__": altered.__name__, "name": Text(unique=True)})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "migrations"
            generate(first, root)
            with self.assertRaises(MigrationGenerationError):
                generate(altered, root)

    def test_generator_preserves_unique_nullable_column_additions(self):
        first = ModuleType("catalog.models")
        first.Product = type("Product", (Entity,), {"__module__": first.__name__, "name": Text()})
        second = ModuleType("catalog.models")
        second.Product = type("Product", (Entity,), {"__module__": second.__name__, "name": Text(), "code": Text(nullable=True, unique=True)})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "migrations"
            generate(first, root)
            source = generate(second, root)
            self.assertIn("ALTER TABLE product_entries ADD COLUMN code TEXT UNIQUE", source.read_text(encoding="utf-8"))
            # Re-running against the same declared schema must stay a no-op:
            # the recorded ADD COLUMN text must match what generate() would
            # declare again, or every unchanged project would fail forever.
            self.assertIsNone(generate(second, root))

    def test_unsafe_schema_identifier_is_rejected(self):
        with self.assertRaisesRegex(MigrationGenerationError, "Unsafe schema identifier"):
            _identifier("1; DROP TABLE users")

    def test_generate_with_no_declared_entities_returns_none(self):
        module = ModuleType("empty.models")
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(generate(module, Path(temp) / "migrations"))

    def test_generator_rejects_a_new_table_without_an_explicit_migration(self):
        first = ModuleType("catalog.models")
        first.Product = type("Product", (Entity,), {"__module__": first.__name__, "name": Text()})
        second = ModuleType("catalog.models")
        second.Product = type("Product", (Entity,), {"__module__": second.__name__, "name": Text()})
        second.Order = type("Order", (Entity,), {"__module__": second.__name__, "total": Integer(default=0)})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "migrations"
            generate(first, root)
            with self.assertRaisesRegex(MigrationGenerationError, "needs an explicit reviewed migration"):
                generate(second, root)

    def test_generator_rejects_a_new_non_nullable_column(self):
        first = ModuleType("catalog.models")
        first.Product = type("Product", (Entity,), {"__module__": first.__name__, "name": Text()})
        second = ModuleType("catalog.models")
        second.Product = type("Product", (Entity,), {"__module__": second.__name__, "name": Text(), "sku": Text()})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "migrations"
            generate(first, root)
            with self.assertRaisesRegex(MigrationGenerationError, "must be nullable"):
                generate(second, root)

    def test_migration_directories_walks_installed_apps_in_order(self):
        import os

        settings = SimpleNamespace(installed_apps=["os"])
        directories = migration_directories(settings)
        self.assertEqual(directories, [os.path.join(os.path.dirname(os.__file__), "migrations")])

    def test_migration_directories_skips_apps_without_a_file_attribute(self):
        settings = SimpleNamespace(installed_apps=["sys"])
        self.assertEqual(migration_directories(settings), [])

    def test_discover_skips_a_directory_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(discover([Path(temp) / "does-not-exist"]), [])

    def test_discover_rejects_a_migration_path_that_is_not_a_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            not_a_directory = Path(temp) / "migrations.txt"
            not_a_directory.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(MigrationFormatError, "not a directory"):
                discover([not_a_directory])

    def test_discover_rejects_duplicate_migration_names_across_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / "first", Path(temp) / "second"
            first.mkdir()
            second.mkdir()
            self._migration(first, "0001_base", ["CREATE TABLE base (id INTEGER)"])
            self._migration(second, "0001_base", ["CREATE TABLE other (id INTEGER)"])
            with self.assertRaisesRegex(MigrationFormatError, "Duplicate migration name"):
                discover([first, second])

    def test_discover_reports_a_migration_it_cannot_load(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._migration(root, "0001_base", ["CREATE TABLE base (id INTEGER)"])
            with patch("hayavega.migrations.importlib.util.spec_from_file_location", return_value=None):
                with self.assertRaisesRegex(MigrationFormatError, "Cannot load migration"):
                    discover([root])

    def test_discover_rejects_operations_that_are_not_a_non_empty_list_of_strings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "0001_base.py").write_text("operations = []", encoding="utf-8")
            with self.assertRaisesRegex(MigrationFormatError, "non-empty list"):
                discover([root])

    def test_discover_rejects_multiple_statements_in_a_single_operation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._migration(root, "0001_base", ["CREATE TABLE base (id INTEGER); DROP TABLE base"])
            with self.assertRaisesRegex(MigrationFormatError, "exactly one SQL statement"):
                discover([root])

    def test_discover_rejects_malformed_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "0001_base.py").write_text("dependencies = 'not-a-list'\noperations = ['CREATE TABLE base (id INTEGER)']", encoding="utf-8")
            with self.assertRaisesRegex(MigrationFormatError, "dependencies must be a list"):
                discover([root])

    def test_discover_rejects_a_missing_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "0001_child.py").write_text("dependencies = ['0000_absent']\noperations = ['CREATE TABLE base (id INTEGER)']", encoding="utf-8")
            with self.assertRaisesRegex(MigrationFormatError, "missing dependencies"):
                discover([root])

    def test_status_rejects_an_applied_migration_whose_file_was_altered(self):
        with tempfile.TemporaryDirectory() as temp:
            root, database = Path(temp) / "migrations", Path(temp) / "data.sqlite3"
            root.mkdir()
            self._migration(root, "0001_products", ["CREATE TABLE products (id INTEGER PRIMARY KEY)"])
            apply(database, [root])
            self._migration(root, "0001_products", ["CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"])
            with self.assertRaisesRegex(MigrationIntegrityError, "was altered"):
                status(database, [root])

    def test_apply_rejects_when_an_applied_migration_file_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root, database = Path(temp) / "migrations", Path(temp) / "data.sqlite3"
            root.mkdir()
            self._migration(root, "0001_products", ["CREATE TABLE products (id INTEGER PRIMARY KEY)"])
            apply(database, [root])
            (root / "0001_products.py").unlink()
            with self.assertRaisesRegex(MigrationIntegrityError, "missing"):
                apply(database, [root])

    def test_dependencies_control_order_and_reject_cycles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._migration(root, "0001_base", ["CREATE TABLE base (id INTEGER)"])
            (root / "0002_child.py").write_text("dependencies = ['0001_base']\noperations = ['CREATE TABLE child (id INTEGER)']", encoding="utf-8")
            self.assertEqual([item.name for item in status(Path(temp) / "data.sqlite3", [root])], ["0001_base", "0002_child"])
            (root / "0001_base.py").write_text("dependencies = ['0002_child']\noperations = ['CREATE TABLE base (id INTEGER)']", encoding="utf-8")
            with self.assertRaises(MigrationFormatError):
                status(Path(temp) / "data.sqlite3", [root])
