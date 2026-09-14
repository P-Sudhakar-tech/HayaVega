import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from mangoo.management.commands import HELP_TEXT, _createadmin, execute_from_command_line
from mangoo.rust_runtime import RustRuntimeUnavailableError


class ManagementTests(unittest.TestCase):
    def test_management_help_lists_supported_commands(self):
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["manage.py"])
        self.assertEqual(output.getvalue().strip(), HELP_TEXT)

    @patch("mangoo.management.commands.run_with_reloader")
    def test_launch_enables_autoreload_by_default(self, run_with_reloader):
        execute_from_command_line(["manage.py", "launch", "8001"])
        run_with_reloader.assert_called_once()

    @patch("mangoo.management.commands.serve")
    @patch("mangoo.management.commands.load_settings")
    def test_launch_accepts_port_argument(self, load_settings, serve):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000)
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["manage.py", "launch", "8001", "--noreload"])
        serve.assert_called_once()
        _, host, port = serve.call_args.args
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8001)

    @patch("mangoo.management.commands.serve")
    @patch("mangoo.management.commands.load_settings")
    def test_launch_defaults_to_the_python_runtime(self, load_settings, serve):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000)
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["manage.py", "launch", "8001", "--noreload"])
        serve.assert_called_once()

    @patch("mangoo.management.commands.serve_rust")
    @patch("mangoo.management.commands.load_settings")
    def test_launch_dispatches_to_the_rust_runtime_when_configured(self, load_settings, serve_rust):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000, server_engine="rust")
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["manage.py", "launch", "8001", "--noreload"])
        serve_rust.assert_called_once()
        _, host, port = serve_rust.call_args.args
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8001)

    @patch("mangoo.management.commands.serve_rust")
    @patch("mangoo.management.commands.load_settings")
    def test_launch_reports_an_unavailable_rust_runtime_without_crashing(self, load_settings, serve_rust):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000, server_engine="rust")
        serve_rust.side_effect = RustRuntimeUnavailableError("mangoo_native was not built")
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        output = io.StringIO()
        with patch.dict(sys.modules, {"test_project_urls": urls}), redirect_stdout(output):
            execute_from_command_line(["manage.py", "launch", "8001", "--noreload"])
        self.assertIn("mangoo_native was not built", output.getvalue())

    @patch("mangoo.management.commands.load_settings")
    def test_createadmin_creates_account(self, load_settings):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "users.sqlite3"
            load_settings.return_value = SimpleNamespace(database={"ENGINE": "sqlite3", "NAME": database})
            prompts = iter(["admin", "admin@example.com"])
            passwords = iter(["Correct-Horse-7!", "Correct-Horse-7!"])
            output = io.StringIO()
            with redirect_stdout(output):
                _createadmin([], prompt=lambda _: next(prompts), password_prompt=lambda _: next(passwords))
            self.assertIn("created successfully", output.getvalue())
            self.assertTrue(database.is_file())

    @patch("mangoo.management.commands.run_with_reloader")
    def test_deprecated_runserver_alias_still_works_and_warns(self, run_with_reloader):
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), patch("sys.stderr", stderr):
            execute_from_command_line(["manage.py", "runserver", "8001"])
        run_with_reloader.assert_called_once()
        self.assertIn("deprecated", stderr.getvalue())
        self.assertIn("launch", stderr.getvalue())

    def test_deprecated_createsuperuser_alias_still_works(self):
        with patch("mangoo.management.commands._createadmin") as createadmin:
            execute_from_command_line(["manage.py", "createsuperuser"])
        createadmin.assert_called_once()

    def test_every_deprecated_alias_maps_to_a_real_command(self):
        from mangoo.management.commands import _DEPRECATED_ALIASES

        for alias, canonical in _DEPRECATED_ALIASES.items():
            self.assertIn(canonical, HELP_TEXT, f"{alias!r} aliases {canonical!r}, which should be documented in HELP_TEXT")

    @patch("mangoo.management.commands.load_settings")
    def test_diagnose_reports_findings_and_a_summary_line(self, load_settings):
        load_settings.return_value = SimpleNamespace(
            module_name="project.settings",
            debug=True,
            secret_key="a-real-unique-secret",
            allowed_hosts=("example.com",),
            installed_apps=(),
            security={"FORCE_HTTPS": True, "SECURE_COOKIES": True},
            database={"ENGINE": "sqlite3", "NAME": Path(tempfile.gettempdir()) / "diagnose_test.sqlite3"},
            base_dir=Path(tempfile.gettempdir()),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["manage.py", "diagnose"])
        text = output.getvalue()
        self.assertIn("DEBUG is enabled", text)
        self.assertIn("warning(s)", text)

    @patch("mangoo.management.commands.run_checks", return_value=[])
    @patch("mangoo.management.commands.load_settings")
    def test_diagnose_reports_no_issues_when_clean(self, load_settings, run_checks):
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["manage.py", "diagnose"])
        self.assertIn("No issues found.", output.getvalue())
