import unittest
from pathlib import Path
from types import ModuleType

from mangoo.conf import load_settings_from_module


class SettingsTests(unittest.TestCase):
    def test_loads_django_style_project_settings(self):
        module = ModuleType("project.settings")
        module.BASE_DIR = Path("C:/projects/demo")
        module.DEBUG = True
        module.ALLOWED_HOSTS = ["localhost"]
        module.INSTALLED_APPS = ["blog"]
        module.MIDDLEWARE = ["mangoo.middleware.SecurityMiddleware"]
        module.ROOT_URLCONF = "project.urls"
        module.DATABASES = {"default": {"ENGINE": "sqlite3", "NAME": "data.sqlite3"}}
        module.SERVER = {"HOST": "127.0.0.1", "PORT": 9000}

        settings = load_settings_from_module(module)

        self.assertTrue(settings.debug)
        self.assertEqual(settings.database_path, Path("C:/projects/demo").resolve() / "data.sqlite3")
        self.assertEqual(settings.server_port, 9000)
        self.assertEqual(settings.root_urlconf, "project.urls")

    def test_accepts_postgresql_database_configuration(self):
        module = ModuleType("project.settings")
        module.DATABASES = {"default": {"ENGINE": "postgresql", "NAME": "mangoo", "HOST": "db.example", "PORT": 5432}}
        module.SERVER = {}
        settings = load_settings_from_module(module)
        self.assertEqual(settings.database["ENGINE"], "postgresql")
        self.assertEqual(settings.database["HOST"], "db.example")

    def test_accepts_mysql_database_configuration(self):
        module = ModuleType("project.settings")
        module.DATABASES = {"default": {"ENGINE": "mysql", "NAME": "mangoo", "HOST": "db.example", "PORT": 3306}}
        module.SERVER = {}
        self.assertEqual(load_settings_from_module(module).database["ENGINE"], "mysql")

    def test_rejects_invalid_server_port(self):
        module = ModuleType("project.settings")
        module.DATABASES = {"default": {"ENGINE": "sqlite3", "NAME": "mangoo.sqlite3"}}
        module.SERVER = {"PORT": 70000}
        with self.assertRaisesRegex(ValueError, "PORT"):
            load_settings_from_module(module)
