import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import patch
import tempfile
from pathlib import Path
import sys

from mangoo.management.commands import _create_superuser, execute_from_command_line


class ManagementTests(unittest.TestCase):
    def test_management_help_lists_supported_commands(self):
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["manage.py"])
        self.assertEqual(output.getvalue().strip(), "Commands: runserver [port] [--noreload], startapp <name>, createsuperuser, enable2fa <username>, makemigrations <app>, migrate, showmigrations")

    @patch("mangoo.management.commands.run_with_reloader")
    def test_runserver_enables_autoreload_by_default(self, run_with_reloader):
        execute_from_command_line(["manage.py", "runserver", "8001"])
        run_with_reloader.assert_called_once()


    @patch("mangoo.management.commands.serve")
    @patch("mangoo.management.commands.load_settings")
    def test_runserver_accepts_port_argument(self, load_settings, serve):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000)
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["manage.py", "runserver", "8001", "--noreload"])
        serve.assert_called_once()
        _, host, port = serve.call_args.args
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8001)

    @patch("mangoo.management.commands.load_settings")
    def test_createsuperuser_creates_account(self, load_settings):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "users.sqlite3"
            load_settings.return_value = SimpleNamespace(database={"ENGINE": "sqlite3", "NAME": database})
            prompts = iter(["admin", "admin@example.com"])
            passwords = iter(["Correct-Horse-7!", "Correct-Horse-7!"])
            output = io.StringIO()
            with redirect_stdout(output):
                _create_superuser([], prompt=lambda _: next(prompts), password_prompt=lambda _: next(passwords))
            self.assertIn("created successfully", output.getvalue())
            self.assertTrue(database.is_file())
