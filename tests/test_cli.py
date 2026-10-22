import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from mangoo.cli import main


class CliTests(unittest.TestCase):
    @patch("mangoo.management.commands.execute_from_command_line")
    def test_launch_forwards_port_to_launch(self, execute):
        main(["launch", "8001"])
        execute.assert_called_once_with(["mango", "launch", "8001"])

    @patch("mangoo.management.commands.execute_from_command_line")
    def test_launch_supports_disabling_autoreload(self, execute):
        main(["launch", "8001", "--noreload"])
        execute.assert_called_once_with(["mango", "launch", "8001", "--noreload"])

    def test_newproject_creates_a_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("mangoo.cli.Path.cwd", return_value=Path(directory)):
                main(["newproject", "demo"])
            self.assertTrue((Path(directory) / "demo" / "mango.py").is_file())

    def test_newapp_creates_an_app(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("mangoo.cli.Path.cwd", return_value=Path(directory)):
                main(["newapp", "blog"])
            self.assertTrue((Path(directory) / "blog" / "views.py").is_file())

    def test_deprecated_startproject_alias_still_works_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with patch("mangoo.cli.Path.cwd", return_value=Path(directory)), redirect_stderr(stderr):
                main(["startproject", "demo"])
            self.assertTrue((Path(directory) / "demo" / "mango.py").is_file())
            self.assertIn("deprecated", stderr.getvalue())
            self.assertIn("newproject", stderr.getvalue())

    def test_deprecated_startapp_alias_still_works_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with patch("mangoo.cli.Path.cwd", return_value=Path(directory)), redirect_stderr(stderr):
                main(["startapp", "blog"])
            self.assertTrue((Path(directory) / "blog" / "views.py").is_file())
            self.assertIn("deprecated", stderr.getvalue())
            self.assertIn("newapp", stderr.getvalue())

    @patch("mangoo.management.commands.execute_from_command_line")
    def test_diagnose_forwards_to_execute_from_command_line(self, execute):
        main(["diagnose"])
        execute.assert_called_once_with(["mango", "diagnose"])

    @patch("mangoo.management.commands.execute_from_command_line")
    def test_cacheserver_forwards_default_host_and_port(self, execute):
        main(["cacheserver"])
        execute.assert_called_once_with(["mango", "cacheserver", "--host", "127.0.0.1", "--port", "6379"])

    @patch("mangoo.management.commands.execute_from_command_line")
    def test_cacheserver_forwards_host_port_and_password(self, execute):
        main(["cacheserver", "--host", "0.0.0.0", "--port", "7000", "--password", "secret"])
        execute.assert_called_once_with(["mango", "cacheserver", "--host", "0.0.0.0", "--port", "7000", "--password", "secret"])
