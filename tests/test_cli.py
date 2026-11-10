import io
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from hayavega.cli import main


class CliTests(unittest.TestCase):
    @patch("hayavega.management.commands.execute_from_command_line")
    def test_launch_forwards_port_to_launch(self, execute):
        # main()'s own "launch" handling sets this real env var
        # unconditionally (by design -- it must propagate to the
        # autoreloader's child process), with no cleanup of its own;
        # addCleanup here keeps that real side effect from leaking into
        # every later test in this process instead of patch.dict, since
        # the assertion needs the attribute actually set afterward.
        self.addCleanup(os.environ.pop, "HAYAVEGA_LAUNCH_MODE", None)
        main(["launch", "8001"])
        execute.assert_called_once_with(["hayavega", "launch", "8001"])

    @patch("hayavega.management.commands.execute_from_command_line")
    def test_launch_supports_disabling_autoreload(self, execute):
        self.addCleanup(os.environ.pop, "HAYAVEGA_LAUNCH_MODE", None)
        main(["launch", "8001", "--noreload"])
        execute.assert_called_once_with(["hayavega", "launch", "8001", "--noreload"])

    def test_newproject_creates_a_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("hayavega.cli.Path.cwd", return_value=Path(directory)):
                main(["newproject", "demo"])
            self.assertTrue((Path(directory) / "demo" / "hayavega.py").is_file())

    def test_newapp_creates_an_app(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("hayavega.cli.Path.cwd", return_value=Path(directory)):
                main(["newapp", "blog"])
            self.assertTrue((Path(directory) / "blog" / "views.py").is_file())

    def test_deprecated_startproject_alias_still_works_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with patch("hayavega.cli.Path.cwd", return_value=Path(directory)), redirect_stderr(stderr):
                main(["startproject", "demo"])
            self.assertTrue((Path(directory) / "demo" / "hayavega.py").is_file())
            self.assertIn("deprecated", stderr.getvalue())
            self.assertIn("newproject", stderr.getvalue())

    def test_deprecated_startapp_alias_still_works_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with patch("hayavega.cli.Path.cwd", return_value=Path(directory)), redirect_stderr(stderr):
                main(["startapp", "blog"])
            self.assertTrue((Path(directory) / "blog" / "views.py").is_file())
            self.assertIn("deprecated", stderr.getvalue())
            self.assertIn("newapp", stderr.getvalue())

    @patch("hayavega.management.commands.execute_from_command_line")
    def test_diagnose_forwards_to_execute_from_command_line(self, execute):
        main(["diagnose"])
        execute.assert_called_once_with(["hayavega", "diagnose"])

    @patch("hayavega.management.commands.execute_from_command_line")
    def test_cacheserver_forwards_default_host_and_port(self, execute):
        main(["cacheserver"])
        execute.assert_called_once_with(["hayavega", "cacheserver", "--host", "127.0.0.1", "--port", "6379"])

    @patch("hayavega.management.commands.execute_from_command_line")
    def test_cacheserver_forwards_host_port_and_password(self, execute):
        main(["cacheserver", "--host", "0.0.0.0", "--port", "7000", "--password", "secret"])
        execute.assert_called_once_with(["hayavega", "cacheserver", "--host", "0.0.0.0", "--port", "7000", "--password", "secret"])


class MainModuleEntryPointTests(unittest.TestCase):
    """Exercises the `if __name__ == "__main__": main()` guard itself,
    which only runs when the module is executed directly, never on a
    plain import -- `runpy.run_module(..., run_name="__main__")` runs the
    real module body with `__name__` set to `"__main__"`, in-process (so
    coverage still sees it, unlike a subprocess), the same mechanism
    `python -m hayavega.cli` itself uses."""

    def test_running_as_the_main_module_dispatches_to_a_real_scaffold_command(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("hayavega.cli.Path.cwd", return_value=Path(directory)), patch.object(sys, "argv", ["hayavega.py", "newapp", "demo"]):
                runpy.run_module("hayavega.cli", run_name="__main__")
            self.assertTrue((Path(directory) / "demo").is_dir())
