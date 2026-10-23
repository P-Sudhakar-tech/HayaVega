import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mangoo.checks import CheckMessage, _scan_deprecated_usage, run_checks


def _settings(**overrides) -> SimpleNamespace:
    defaults = {
        "base_dir": Path(tempfile.gettempdir()),
        "module_name": "config.settings",
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

    @patch("mangoo.checks.migration_status")
    def test_pending_migrations_are_reported_as_a_warning_naming_each_one(self, mock_migration_status):
        from mangoo.migrations import MigrationStatus

        mock_migration_status.return_value = [MigrationStatus("0001_initial", True), MigrationStatus("0002_add_index", False)]
        messages = run_checks(_settings())
        matches = [message for message in messages if "pending migration" in message.message]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].level, "warning")
        self.assertIn("0002_add_index", matches[0].message)
        self.assertNotIn("0001_initial", matches[0].message)


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

    def test_unreadable_or_non_utf8_file_is_skipped_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "legacy.py").write_bytes(b"\xff\xfe# mentions PulseBus\x00")
            messages = run_checks(_settings(base_dir=base_dir))
            self.assertFalse(any("legacy.py" in message.message for message in messages))

    def test_a_path_that_relative_to_cannot_express_falls_back_to_the_absolute_path(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "views.py").write_text("PulseBus\n", encoding="utf-8")
            with patch("mangoo.checks.Path.relative_to", side_effect=ValueError):
                messages = _scan_deprecated_usage(base_dir)
            self.assertTrue(any("PulseBus" in message.message for message in messages))


class CryptoCheckTests(unittest.TestCase):
    @patch("mangoo.checks.crypto.available", return_value=True)
    def test_no_finding_when_cryptography_is_installed(self, _available):
        messages = run_checks(_settings())
        self.assertFalse(any("cryptography" in message.message for message in messages))

    @patch("mangoo.checks.crypto.available", return_value=False)
    def test_info_finding_when_cryptography_is_not_installed(self, _available):
        messages = run_checks(_settings())
        matches = [message for message in messages if "cryptography" in message.message]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].level, "info")


class RunChecksOrderingTests(unittest.TestCase):
    def test_errors_are_reported_before_warnings(self):
        messages = run_checks(_settings(debug=False, secret_key="", allowed_hosts=(), security={}))
        levels = [message.level for message in messages]
        self.assertEqual(levels, sorted(levels, key=lambda level: {"error": 0, "warning": 1, "info": 2}.get(level, 3)))

    @patch("mangoo.checks._scan_deprecated_usage", return_value=[])
    @patch("mangoo.checks._pending_migration_checks", return_value=[])
    @patch("mangoo.checks._crypto_checks", return_value=[])
    @patch("mangoo.checks._settings_checks", return_value=[])
    def test_a_clean_project_reports_no_issues(self, *_mocks):
        self.assertEqual(run_checks(_settings()), [])
