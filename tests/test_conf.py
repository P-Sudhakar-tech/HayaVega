import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

from hayavega.conf import load_database, load_settings_from_module


def _module(**settings_kwargs) -> ModuleType:
    module = ModuleType("config.settings")
    module.settings = SimpleNamespace(**settings_kwargs)
    return module


class SettingsTests(unittest.TestCase):
    def test_loads_hayavega_style_project_settings(self):
        module = _module(
            base_dir=Path("C:/projects/demo"),
            debug=True,
            allowed_hosts=["localhost"],
            installed_apps=["blog"],
            middleware=["hayavega.middleware.SecurityMiddleware"],
            root_urlconf="config.routes",
            database={"engine": "sqlite3", "name": "data.sqlite3"},
            server={"host": "127.0.0.1", "port": 9000},
        )

        settings = load_settings_from_module(module)

        self.assertTrue(settings.debug)
        self.assertEqual(settings.database_path, Path("C:/projects/demo").resolve() / "data.sqlite3")
        self.assertEqual(settings.server_port, 9000)
        self.assertEqual(settings.root_urlconf, "config.routes")

    def test_accepts_a_nested_class_as_a_section_not_just_a_dict(self):
        class database:
            engine = "sqlite3"
            name = "data.sqlite3"

        module = _module(database=database, server={})
        settings = load_settings_from_module(module)
        self.assertEqual(settings.database["ENGINE"], "sqlite3")

    def test_accepts_postgresql_database_configuration(self):
        module = _module(database={"engine": "postgresql", "name": "hayavega", "host": "db.example", "port": 5432}, server={})
        settings = load_settings_from_module(module)
        self.assertEqual(settings.database["ENGINE"], "postgresql")
        self.assertEqual(settings.database["HOST"], "db.example")

    def test_accepts_mysql_database_configuration(self):
        module = _module(database={"engine": "mysql", "name": "hayavega", "host": "db.example", "port": 3306}, server={})
        self.assertEqual(load_settings_from_module(module).database["ENGINE"], "mysql")

    def test_missing_settings_object_is_rejected(self):
        module = ModuleType("config.settings")
        with self.assertRaisesRegex(ValueError, "settings"):
            load_settings_from_module(module)

    def test_missing_database_name_is_rejected(self):
        module = _module(database={"engine": "sqlite3"}, server={})
        with self.assertRaisesRegex(ValueError, "Settings.database.name"):
            load_settings_from_module(module)

    def test_rejects_invalid_server_port(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={"port": 70000})
        with self.assertRaisesRegex(ValueError, "Settings.server.port"):
            load_settings_from_module(module)

    def test_server_engine_defaults_to_python(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={})
        self.assertEqual(load_settings_from_module(module).server_engine, "python")

    def test_server_engine_accepts_rust(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={"engine": "rust"})
        self.assertEqual(load_settings_from_module(module).server_engine, "rust")

    def test_rejects_invalid_server_engine(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={"engine": "nodejs"})
        with self.assertRaisesRegex(ValueError, "Settings.server.engine"):
            load_settings_from_module(module)

    def test_server_workers_defaults_to_eight(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={})
        self.assertEqual(load_settings_from_module(module).server_workers, 8)

    def test_server_workers_is_configurable(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={"workers": 16})
        self.assertEqual(load_settings_from_module(module).server_workers, 16)

    def test_rejects_non_positive_server_workers(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={"workers": 0})
        with self.assertRaisesRegex(ValueError, "Settings.server.workers"):
            load_settings_from_module(module)

    def test_server_processes_defaults_to_one(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={})
        self.assertEqual(load_settings_from_module(module).server_processes, 1)

    def test_server_processes_is_configurable_with_the_rust_engine(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={"engine": "rust", "processes": 4})
        self.assertEqual(load_settings_from_module(module).server_processes, 4)

    def test_rejects_non_positive_server_processes(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={"processes": 0})
        with self.assertRaisesRegex(ValueError, "Settings.server.processes"):
            load_settings_from_module(module)

    def test_rejects_multiprocess_under_the_python_engine(self):
        """processes>1 needs SO_REUSEPORT, which hayavega.runtime's wsgiref
        server has no multi-process story for in this version -- same
        "fail loudly, don't silently ignore" precedent as tls_cert/tls_key
        under the python engine."""
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={"engine": "python", "processes": 2})
        with self.assertRaisesRegex(ValueError, "Settings.server.processes"):
            load_settings_from_module(module)

    def test_security_section_passes_through_to_the_uppercase_dict_other_modules_consume(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={}, security={"force_https": True, "custom_flag": "x"})
        settings = load_settings_from_module(module)
        self.assertEqual(settings.security["FORCE_HTTPS"], True)
        self.assertEqual(settings.security["CUSTOM_FLAG"], "x")

    def test_cache_section_passes_through_to_the_uppercase_dict_cache_from_settings_consumes(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={}, cache={"backend": "redis", "redis_url": "redis://cache.internal:6379/1"})
        settings = load_settings_from_module(module)
        self.assertEqual(settings.cache["BACKEND"], "redis")
        self.assertEqual(settings.cache["REDIS_URL"], "redis://cache.internal:6379/1")

    def test_cache_section_defaults_to_empty_when_unset(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={})
        self.assertEqual(load_settings_from_module(module).cache, {})

    def test_i18n_section_passes_through_to_the_uppercase_dict_catalog_from_settings_consumes(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={}, i18n={"languages": ["en", "es"], "default_language": "en"})
        settings = load_settings_from_module(module)
        self.assertEqual(settings.i18n["LANGUAGES"], ["en", "es"])
        self.assertEqual(settings.i18n["DEFAULT_LANGUAGE"], "en")

    def test_i18n_section_defaults_to_empty_when_unset(self):
        module = _module(database={"engine": "sqlite3", "name": "hayavega.sqlite3"}, server={})
        self.assertEqual(load_settings_from_module(module).i18n, {})


class LoadDatabaseTests(unittest.TestCase):
    """A project with more than one database (e.g. a secondary 'analytics'
    connection beyond the primary `Settings.database`) defines its own
    extra named section and converts it with this same public helper --
    see docs/topics/models.md#multiple-databases."""

    def test_validates_and_uppercases_a_standalone_section(self):
        database = load_database({"engine": "postgresql", "name": "warehouse", "host": "analytics.internal"}, label="Settings.databases.analytics")
        self.assertEqual(database, {"ENGINE": "postgresql", "NAME": "warehouse", "HOST": "analytics.internal"})

    def test_accepts_a_nested_class_section_too(self):
        class analytics:
            engine = "sqlite3"
            name = "analytics.sqlite3"

        database = load_database(analytics, base_dir=Path("/srv/app"))
        self.assertEqual(database["NAME"], Path("/srv/app/analytics.sqlite3"))

    def test_resolves_relative_sqlite_name_against_base_dir(self):
        database = load_database({"engine": "sqlite3", "name": "warehouse.sqlite3"}, base_dir=Path("/srv/app"))
        self.assertEqual(database["NAME"], Path("/srv/app/warehouse.sqlite3"))

    def test_relative_sqlite_name_without_base_dir_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "base_dir"):
            load_database({"engine": "sqlite3", "name": "warehouse.sqlite3"})

    def test_missing_name_is_rejected_with_the_given_label(self):
        with self.assertRaisesRegex(ValueError, "Settings.databases.analytics.name"):
            load_database({"engine": "postgresql"}, label="Settings.databases.analytics")

    def test_invalid_engine_is_rejected_with_the_given_label(self):
        with self.assertRaisesRegex(ValueError, "Settings.databases.analytics.engine"):
            load_database({"engine": "mongodb", "name": "x"}, label="Settings.databases.analytics")
