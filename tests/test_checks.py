import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mangoo.checks import CheckMessage, run_checks


def _settings(**overrides) -> SimpleNamespace:
    defaults = {
        "base_dir": Path(tempfile.gettempdir()),
        "module_name": "project.settings",
        "debug": True,
        "secret_key": "a-real-unique-secret",
        "allowed_hosts": ("example.com",),
        "installed_apps": (),
        "security": {"FORCE_HTTPS": True, "SECURE_COOKIES": True},
        "database": {"ENGINE": "sqlite3", "NAME": Path(tempfile.gettempdir()) / "checks_test.sqlite3"},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class CheckMessageTests(unittest.TestCase):
    def test_str_includes_level_message_and_hint(self):
        message = CheckMessage("warning", "DEBUG is enabled.", "Set DEBUG = False.")
        text = str(message)
        self.assertIn("[WARNING]", text)
        self.assertIn("DEBUG is enabled.", text)
        self.assertIn("Set DEBUG = False.", text)

    def test_str_omits_hint_line_when_no_hint(self):
        message = CheckMessage("error", "Something is wrong.")
        self.assertNotIn("->", str(message))


class SettingsChecksTests(unittest.TestCase):
    def test_debug_true_produces_a_warning(self):
        messages = run_checks(_settings(debug=True))
        self.assertTrue(any("DEBUG is enabled" in message.message for message in messages))

    def test_safe_debug_false_settings_produce_no_settings_warnings(self):
        messages = run_checks(_settings(debug=False))
        self.assertFalse(any("DEBUG is enabled" in message.message for message in messages))
        self.assertFalse(any("SECRET_KEY" in message.message for message in messages))
        self.assertFalse(any("ALLOWED_HOSTS" in message.message for message in messages))

    def test_placeholder_secret_key_is_an_error_in_production(self):
        messages = run_checks(_settings(debug=False, secret_key="change-this-in-production"))
        matches = [message for message in messages if "SECRET_KEY" in message.message]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].level, "error")

    def test_wildcard_allowed_hosts_is_an_error_in_production(self):
        messages = run_checks(_settings(debug=False, allowed_hosts=("*",)))
        matches = [message for message in messages if "ALLOWED_HOSTS" in message.message]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].level, "error")

    def test_missing_https_and_cookie_security_are_warnings_in_production(self):
        messages = run_checks(_settings(debug=False, security={}))
        self.assertTrue(any("FORCE_HTTPS" in message.message for message in messages))
        self.assertTrue(any("SECURE_COOKIES" in message.message for message in messages))

    def test_every_hint_names_the_settings_module(self):
        messages = run_checks(_settings(debug=True, module_name="myproject.settings"))
        settings_messages = [message for message in messages if message.hint]
        self.assertTrue(settings_messages)
        self.assertTrue(any("myproject.settings" in message.hint for message in settings_messages))


class PendingMigrationCheckTests(unittest.TestCase):
    def test_no_installed_apps_means_no_migration_findings(self):
        messages = run_checks(_settings(installed_apps=()))
        self.assertFalse(any("pending migration" in message.message for message in messages))

    def test_unreachable_database_is_reported_as_an_error(self):
        settings = _settings(database={"ENGINE": "postgresql", "NAME": "nope", "HOST": "does-not-exist.invalid", "PORT": 5432, "USER": "x", "PASSWORD": "x"})
        messages = run_checks(settings)
        matches = [message for message in messages if "Could not determine migration status" in message.message]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].level, "error")


class DeprecatedUsageScanTests(unittest.TestCase):
    def test_detects_deprecated_pulse_import(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "views.py").write_text("from mangoo.addons.pulse import PulseBus\n", encoding="utf-8")
            messages = run_checks(_settings(base_dir=base_dir))
            findings = [message for message in messages if "views.py" in message.message]
            self.assertTrue(any("mangoo.addons.pulse" in message.message for message in findings))
            self.assertTrue(any("PulseBus" in message.message for message in findings))

    def test_detects_deprecated_pulse_keyword_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "urls.py").write_text("create_application(urlpatterns, pulse=bus)\n", encoding="utf-8")
            messages = run_checks(_settings(base_dir=base_dir))
            self.assertTrue(any("pulse=" in message.message for message in messages))

    def test_clean_project_has_no_deprecated_usage_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "urls.py").write_text("from mangoo.addons.signals import SignalBus\ncreate_application(urlpatterns, signals=bus)\n", encoding="utf-8")
            messages = run_checks(_settings(base_dir=base_dir))
            self.assertFalse(any("deprecated" in message.message for message in messages))

    def test_migrations_directory_is_excluded_from_the_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            migrations_dir = base_dir / "blog" / "migrations"
            migrations_dir.mkdir(parents=True)
            (migrations_dir / "0001_initial.py").write_text("# mentions PulseBus for historical reasons\n", encoding="utf-8")
            messages = run_checks(_settings(base_dir=base_dir))
            self.assertFalse(any("PulseBus" in message.message for message in messages))

    def test_missing_base_dir_does_not_crash(self):
        messages = run_checks(_settings(base_dir=Path("/this/does/not/exist")))
        self.assertIsInstance(messages, list)


class RunChecksOrderingTests(unittest.TestCase):
    def test_errors_are_reported_before_warnings(self):
        messages = run_checks(_settings(debug=False, secret_key="", allowed_hosts=(), security={}))
        levels = [message.level for message in messages]
        self.assertEqual(levels, sorted(levels, key=lambda level: {"error": 0, "warning": 1, "info": 2}.get(level, 3)))

    @patch("mangoo.checks._scan_deprecated_usage", return_value=[])
    @patch("mangoo.checks._pending_migration_checks", return_value=[])
    @patch("mangoo.checks._settings_checks", return_value=[])
    def test_a_clean_project_reports_no_issues(self, *_mocks):
        self.assertEqual(run_checks(_settings()), [])
