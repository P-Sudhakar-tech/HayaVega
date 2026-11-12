import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from hayavega import Entity, Text
from hayavega.auth import create_user, min_length_validator
from hayavega.management.commands import HELP_TEXT, _createadmin, execute_from_command_line
from hayavega.rust_runtime import RustRuntimeUnavailableError

# A project references a *pre-configured* validator by dotted path (the
# resolution mechanism only loads a plain callable, not a factory-call
# expression) -- this mirrors how a real project would put
# `my_validator = min_length_validator(8)` in its own module and point
# `Settings.password_validators` at that name.
_relaxed_password_validator = min_length_validator(4)


class ManagementTests(unittest.TestCase):
    def test_management_help_lists_supported_commands(self):
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py"])
        self.assertEqual(output.getvalue().strip(), HELP_TEXT)

    @patch.dict("os.environ", {"HAYAVEGA_LAUNCH_MODE": ""})
    @patch("hayavega.management.commands.run_with_reloader")
    def test_launch_enables_autoreload_by_default(self, run_with_reloader):
        # Explicitly cleared rather than relying on no other test having
        # set it first: hayavega.cli's own "launch" handling sets this real
        # env var permanently, by design, with no cleanup of its own.
        execute_from_command_line(["hayavega.py", "launch", "8001"])
        run_with_reloader.assert_called_once()
        self.assertEqual(run_with_reloader.call_args.args[0][1], sys.argv[0])

    @patch("hayavega.management.commands.serve")
    @patch("hayavega.management.commands.load_settings")
    def test_launch_accepts_port_argument(self, load_settings, serve):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000)
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["hayavega.py", "launch", "8001", "--noreload"])
        serve.assert_called_once()
        _, host, port = serve.call_args.args
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8001)

    @patch("hayavega.management.commands.serve")
    @patch("hayavega.management.commands.load_settings")
    def test_launch_defaults_to_the_python_runtime(self, load_settings, serve):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000)
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["hayavega.py", "launch", "8001", "--noreload"])
        serve.assert_called_once()

    @patch("hayavega.management.commands.serve_rust")
    @patch("hayavega.management.commands.load_settings")
    def test_launch_dispatches_to_the_rust_runtime_when_configured(self, load_settings, serve_rust):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000, server_engine="rust")
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["hayavega.py", "launch", "8001", "--noreload"])
        serve_rust.assert_called_once()
        _, host, port = serve_rust.call_args.args
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8001)

    @patch("hayavega.management.commands.serve_rust")
    @patch("hayavega.management.commands.load_settings")
    def test_launch_passes_workers_and_processes_from_settings_to_the_rust_runtime(self, load_settings, serve_rust):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000, server_engine="rust", server_workers=16, server_processes=4)
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["hayavega.py", "launch", "8001", "--noreload"])
        serve_rust.assert_called_once()
        self.assertEqual(serve_rust.call_args.kwargs["workers"], 16)
        self.assertEqual(serve_rust.call_args.kwargs["processes"], 4)

    @patch("hayavega.management.commands.serve_rust")
    @patch("hayavega.management.commands.load_settings")
    def test_launch_reports_an_unavailable_rust_runtime_without_crashing(self, load_settings, serve_rust):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000, server_engine="rust")
        serve_rust.side_effect = RustRuntimeUnavailableError("hayavega_native was not built")
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        output = io.StringIO()
        with patch.dict(sys.modules, {"test_project_urls": urls}), redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "launch", "8001", "--noreload"])
        self.assertIn("hayavega_native was not built", output.getvalue())

    @patch("hayavega.management.commands.load_settings")
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

    @patch("hayavega.management.commands.load_settings")
    def test_createadmin_uses_configured_password_validators(self, load_settings):
        """A password the default `validate_password` would reject (no
        symbol here) succeeds once `Settings.password_validators` names
        a relaxed custom validator instead."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "users.sqlite3"
            load_settings.return_value = SimpleNamespace(database={"ENGINE": "sqlite3", "NAME": database}, password_validators=("test_management._relaxed_password_validator",))
            prompts = iter(["admin", "admin@example.com"])
            passwords = iter(["short", "short"])
            output = io.StringIO()
            with redirect_stdout(output):
                _createadmin([], prompt=lambda _: next(prompts), password_prompt=lambda _: next(passwords))
            self.assertIn("created successfully", output.getvalue())

    @patch("hayavega.management.commands.run_with_reloader")
    def test_deprecated_runserver_alias_still_works_and_warns(self, run_with_reloader):
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), patch("sys.stderr", stderr):
            execute_from_command_line(["hayavega.py", "runserver", "8001"])
        run_with_reloader.assert_called_once()
        self.assertIn("deprecated", stderr.getvalue())
        self.assertIn("launch", stderr.getvalue())

    def test_deprecated_createsuperuser_alias_still_works(self):
        with patch("hayavega.management.commands._createadmin") as createadmin:
            execute_from_command_line(["hayavega.py", "createsuperuser"])
        createadmin.assert_called_once()

    def test_every_deprecated_alias_maps_to_a_real_command(self):
        from hayavega.management.commands import _DEPRECATED_ALIASES

        for alias, canonical in _DEPRECATED_ALIASES.items():
            self.assertIn(canonical, HELP_TEXT, f"{alias!r} aliases {canonical!r}, which should be documented in HELP_TEXT")

    @patch("hayavega.management.commands.load_settings")
    def test_diagnose_reports_findings_and_a_summary_line(self, load_settings):
        load_settings.return_value = SimpleNamespace(
            module_name="config.settings",
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
            execute_from_command_line(["hayavega.py", "diagnose"])
        text = output.getvalue()
        self.assertIn("DEBUG is enabled", text)
        self.assertIn("warning(s)", text)

    @patch("hayavega.management.commands.run_checks", return_value=[])
    @patch("hayavega.management.commands.load_settings")
    def test_diagnose_reports_no_issues_when_clean(self, load_settings, run_checks):
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "diagnose"])
        self.assertIn("No issues found.", output.getvalue())

    @patch("hayavega.management.commands.serve_cache")
    def test_cacheserver_defaults_to_localhost_and_redis_cache_port(self, serve_cache):
        execute_from_command_line(["hayavega.py", "cacheserver"])
        serve_cache.assert_called_once_with("127.0.0.1", 6379, password=None)

    @patch("hayavega.management.commands.serve_cache")
    def test_cacheserver_accepts_host_port_and_password_flags(self, serve_cache):
        execute_from_command_line(["hayavega.py", "cacheserver", "--host", "0.0.0.0", "--port", "7000", "--password", "secret"])
        serve_cache.assert_called_once_with("0.0.0.0", 7000, password="secret")

    @patch("hayavega.management.commands.serve_cache", side_effect=ValueError("needs a password"))
    def test_cacheserver_reports_a_rejected_configuration_without_crashing(self, serve_cache):
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "cacheserver", "--host", "0.0.0.0"])
        self.assertIn("needs a password", output.getvalue())


_custom_command_calls: list[list[str]] = []


def _hello_command(args: list[str]) -> None:
    _custom_command_calls.append(args)
    print(f"hello {' '.join(args)}".rstrip())


class CustomCommandTests(unittest.TestCase):
    def setUp(self):
        _custom_command_calls.clear()

    @patch("hayavega.management.commands.load_settings")
    def test_a_registered_custom_command_is_dispatched_with_its_remaining_args(self, load_settings):
        load_settings.return_value = SimpleNamespace(commands={"hello": "test_management._hello_command"})
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "hello", "world"])
        self.assertEqual(_custom_command_calls, [["world"]])
        self.assertIn("hello world", output.getvalue())

    @patch("hayavega.management.commands.load_settings")
    def test_an_unregistered_command_still_falls_through_to_help_text(self, load_settings):
        load_settings.return_value = SimpleNamespace(commands={"hello": "test_management._hello_command"})
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "not-a-real-command"])
        self.assertEqual(output.getvalue().strip(), HELP_TEXT)

    @patch("hayavega.management.commands.load_settings", side_effect=ModuleNotFoundError("No module named 'config'"))
    def test_falls_through_to_help_text_when_settings_cannot_be_loaded_at_all(self, load_settings):
        """`hayavega.py` run outside a real project (no `config.settings`
        module) must still show HELP_TEXT, not an unrelated import
        error, for any unrecognized command."""
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "not-a-real-command"])
        self.assertEqual(output.getvalue().strip(), HELP_TEXT)

    @patch("hayavega.management.commands.load_settings")
    def test_builtin_commands_are_never_shadowed_by_a_custom_registration(self, load_settings):
        load_settings.return_value = SimpleNamespace(commands={"diagnose": "test_management._hello_command"})
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "diagnose"])
        self.assertEqual(_custom_command_calls, [])


class NewappDispatchTests(unittest.TestCase):
    def test_newapp_dispatch_creates_a_real_app_via_the_scaffold(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("hayavega.cli.Path.cwd", return_value=Path(directory)), patch("os.getcwd", return_value=directory):
                output = io.StringIO()
                with redirect_stdout(output), patch("pathlib.Path.cwd", return_value=Path(directory)):
                    execute_from_command_line(["hayavega.py", "newapp", "blog"])
            self.assertTrue((Path(directory) / "blog").is_dir())


class CreateAdminFailureTests(unittest.TestCase):
    @patch("hayavega.management.commands.load_settings")
    def test_mismatched_passwords_are_rejected(self, load_settings):
        with tempfile.TemporaryDirectory() as directory:
            load_settings.return_value = SimpleNamespace(database={"ENGINE": "sqlite3", "NAME": Path(directory) / "users.sqlite3"})
            prompts = iter(["admin", "admin@example.com"])
            passwords = iter(["Correct-Horse-7!", "Different-Horse-9!"])
            output = io.StringIO()
            with redirect_stdout(output):
                _createadmin([], prompt=lambda _: next(prompts), password_prompt=lambda _: next(passwords))
            self.assertIn("do not match", output.getvalue())

    @patch("hayavega.management.commands.load_settings")
    def test_a_duplicate_username_is_reported_cleanly(self, load_settings):
        with tempfile.TemporaryDirectory() as directory:
            database = {"ENGINE": "sqlite3", "NAME": Path(directory) / "users.sqlite3"}
            load_settings.return_value = SimpleNamespace(database=database)
            create_user(database, "admin", "admin@example.com", "Correct-Horse-7!")
            prompts = iter(["admin", "admin2@example.com"])
            passwords = iter(["Correct-Horse-7!", "Correct-Horse-7!"])
            output = io.StringIO()
            with redirect_stdout(output):
                _createadmin([], prompt=lambda _: next(prompts), password_prompt=lambda _: next(passwords))
            self.assertIn("Error:", output.getvalue())


class Enable2faTests(unittest.TestCase):
    @patch("hayavega.management.commands.load_settings")
    def test_enables_totp_for_a_real_user(self, load_settings):
        with tempfile.TemporaryDirectory() as directory:
            database = {"ENGINE": "sqlite3", "NAME": Path(directory) / "users.sqlite3"}
            load_settings.return_value = SimpleNamespace(database=database, secret_key="test-secret")
            create_user(database, "alice", "alice@example.com", "Correct-Horse-7!")
            output = io.StringIO()
            with redirect_stdout(output):
                execute_from_command_line(["hayavega.py", "enable2fa", "alice"])
            self.assertIn("enabled for 'alice'", output.getvalue())
            self.assertIn("authenticator app", output.getvalue())

    @patch("hayavega.management.commands.load_settings")
    def test_an_unknown_username_is_reported_cleanly(self, load_settings):
        with tempfile.TemporaryDirectory() as directory:
            database = {"ENGINE": "sqlite3", "NAME": Path(directory) / "users.sqlite3"}
            load_settings.return_value = SimpleNamespace(database=database, secret_key="test-secret")
            output = io.StringIO()
            with redirect_stdout(output):
                execute_from_command_line(["hayavega.py", "enable2fa", "nobody"])
            self.assertIn("Error:", output.getvalue())


class MigrationCommandsTests(unittest.TestCase):
    """`newmigration`/`applymigrations`/`migrationstatus` for real: a real
    sqlite database, a real generated migration file, a real apply."""

    def _settings_for(self, directory: Path, app_module):
        database = {"ENGINE": "sqlite3", "NAME": directory / "app.sqlite3"}
        return SimpleNamespace(database=database, installed_apps=[app_module.__name__])

    def test_newmigration_rejects_an_app_not_in_installed_apps(self):
        settings = SimpleNamespace(installed_apps=[])
        with patch("hayavega.management.commands.load_settings", return_value=settings):
            output = io.StringIO()
            with redirect_stdout(output):
                execute_from_command_line(["hayavega.py", "newmigration", "blog"])
            self.assertIn("not in Settings.installed_apps", output.getvalue())

    def test_newmigration_generates_a_real_migration_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_module = ModuleType("mgmt_test_blog")
            app_module.__file__ = str(root / "mgmt_test_blog" / "__init__.py")
            (root / "mgmt_test_blog").mkdir()
            models_module = ModuleType("mgmt_test_blog.models")
            models_module.Post = type("Post", (Entity,), {"__module__": models_module.__name__, "title": Text()})
            with patch.dict(sys.modules, {"mgmt_test_blog": app_module, "mgmt_test_blog.models": models_module}):
                settings = self._settings_for(root, app_module)
                with patch("hayavega.management.commands.load_settings", return_value=settings):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        execute_from_command_line(["hayavega.py", "newmigration", "mgmt_test_blog"])
                    self.assertIn("Created", output.getvalue())

                    # A second run with no entity changes has nothing new to generate.
                    output2 = io.StringIO()
                    with redirect_stdout(output2):
                        execute_from_command_line(["hayavega.py", "newmigration", "mgmt_test_blog"])
                    self.assertIn("No entities found", output2.getvalue())

    def test_newmigration_reports_an_app_with_no_discoverable_file_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_module = ModuleType("mgmt_test_no_file_app")  # deliberately no __file__ set
            models_module = ModuleType("mgmt_test_no_file_app.models")
            with patch.dict(sys.modules, {"mgmt_test_no_file_app": app_module, "mgmt_test_no_file_app.models": models_module}):
                settings = self._settings_for(root, app_module)
                with patch("hayavega.management.commands.load_settings", return_value=settings):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        execute_from_command_line(["hayavega.py", "newmigration", "mgmt_test_no_file_app"])
                    self.assertIn("no discoverable file path", output.getvalue())

    def test_newmigration_reports_a_rejected_change_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_module = ModuleType("mgmt_test_rejected_app")
            app_module.__file__ = str(root / "mgmt_test_rejected_app" / "__init__.py")
            (root / "mgmt_test_rejected_app").mkdir()
            models_module = ModuleType("mgmt_test_rejected_app.models")
            models_module.Note = type("Note", (Entity,), {"__module__": models_module.__name__, "title": Text()})
            with patch.dict(sys.modules, {"mgmt_test_rejected_app": app_module, "mgmt_test_rejected_app.models": models_module}):
                settings = self._settings_for(root, app_module)
                with patch("hayavega.management.commands.load_settings", return_value=settings):
                    first_output = io.StringIO()
                    with redirect_stdout(first_output):
                        execute_from_command_line(["hayavega.py", "newmigration", "mgmt_test_rejected_app"])
                    self.assertIn("Created", first_output.getvalue())

                    # A new, non-nullable column needs an explicit reviewed
                    # migration -- generate() must reject it, not guess a backfill.
                    models_module.Note = type("Note", (Entity,), {"__module__": models_module.__name__, "title": Text(), "body": Text()})
                    second_output = io.StringIO()
                    with redirect_stdout(second_output):
                        execute_from_command_line(["hayavega.py", "newmigration", "mgmt_test_rejected_app"])
                    self.assertIn("Error:", second_output.getvalue())
                    self.assertIn("must be nullable", second_output.getvalue())

    def test_applymigrations_and_migrationstatus_with_nothing_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_module = ModuleType("mgmt_test_empty_app")
            app_module.__file__ = str(root / "mgmt_test_empty_app" / "__init__.py")
            (root / "mgmt_test_empty_app" / "migrations").mkdir(parents=True)
            settings = self._settings_for(root, app_module)
            with patch.dict(sys.modules, {"mgmt_test_empty_app": app_module}), patch("hayavega.management.commands.load_settings", return_value=settings):
                output = io.StringIO()
                with redirect_stdout(output):
                    execute_from_command_line(["hayavega.py", "applymigrations"])
                self.assertIn("No migrations to apply", output.getvalue())

                output2 = io.StringIO()
                with redirect_stdout(output2):
                    execute_from_command_line(["hayavega.py", "migrationstatus"])
                self.assertIn("No migrations found", output2.getvalue())

    def test_applymigrations_and_migrationstatus_with_a_real_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_module = ModuleType("mgmt_test_real_app")
            app_module.__file__ = str(root / "mgmt_test_real_app" / "__init__.py")
            (root / "mgmt_test_real_app").mkdir()
            models_module = ModuleType("mgmt_test_real_app.models")
            models_module.Comment = type("Comment", (Entity,), {"__module__": models_module.__name__, "body": Text()})
            with patch.dict(sys.modules, {"mgmt_test_real_app": app_module, "mgmt_test_real_app.models": models_module}):
                settings = self._settings_for(root, app_module)
                with patch("hayavega.management.commands.load_settings", return_value=settings):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        execute_from_command_line(["hayavega.py", "newmigration", "mgmt_test_real_app"])
                    self.assertIn("Created", output.getvalue())

                    status_output = io.StringIO()
                    with redirect_stdout(status_output):
                        execute_from_command_line(["hayavega.py", "migrationstatus"])
                    self.assertIn("[ ]", status_output.getvalue())

                    apply_output = io.StringIO()
                    with redirect_stdout(apply_output):
                        execute_from_command_line(["hayavega.py", "applymigrations"])
                    self.assertIn("Applied:", apply_output.getvalue())

                    status_output2 = io.StringIO()
                    with redirect_stdout(status_output2):
                        execute_from_command_line(["hayavega.py", "migrationstatus"])
                    self.assertIn("[X]", status_output2.getvalue())


class RunworkerTests(unittest.TestCase):
    @patch("hayavega.management.commands.run_worker")
    @patch("hayavega.management.commands.load_settings")
    def test_runworker_resolves_real_handlers_and_starts_the_real_worker(self, load_settings, run_worker):
        load_settings.return_value = SimpleNamespace(database={"ENGINE": "sqlite3", "NAME": "unused"}, job_handlers={"send_email": "test_management._hello_command"})
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "runworker"])
        self.assertIn("1 handler(s) registered", output.getvalue())
        run_worker.assert_called_once()
        (_database, handlers), _kwargs = run_worker.call_args
        self.assertIs(handlers["send_email"], _hello_command)

    @patch("hayavega.management.commands.run_worker", side_effect=KeyboardInterrupt)
    @patch("hayavega.management.commands.load_settings")
    def test_runworker_stops_cleanly_on_ctrl_c(self, load_settings, run_worker):
        load_settings.return_value = SimpleNamespace(database={"ENGINE": "sqlite3", "NAME": "unused"}, job_handlers={})
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "runworker"])
        self.assertIn("job worker stopped", output.getvalue())


class LaunchEdgeCaseTests(unittest.TestCase):
    @patch.dict("os.environ", {"HAYAVEGA_LAUNCH_MODE": "module", "HAYAVEGA_RUN_MAIN": ""})
    @patch("hayavega.management.commands.run_with_reloader")
    def test_module_launch_mode_builds_a_python_dash_m_command(self, run_with_reloader):
        execute_from_command_line(["hayavega.py", "launch", "8001"])
        command = run_with_reloader.call_args.args[0]
        self.assertEqual(command[1:4], ["-m", "hayavega.cli", "launch"])

    @patch("hayavega.management.commands.serve")
    @patch("hayavega.management.commands.load_settings")
    def test_a_host_colon_port_address_is_parsed_into_both_parts(self, load_settings, serve):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000)
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            execute_from_command_line(["hayavega.py", "launch", "0.0.0.0:9000", "--noreload"])
        _, host, port = serve.call_args.args
        self.assertEqual(host, "0.0.0.0")
        self.assertEqual(port, 9000)

    @patch("hayavega.management.commands.validate_production", side_effect=__import__("hayavega.production", fromlist=["ProductionConfigurationError"]).ProductionConfigurationError("unsafe secret key"))
    @patch("hayavega.management.commands.load_settings")
    def test_a_production_configuration_error_is_reported_without_crashing(self, load_settings, validate_production):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000)
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "launch", "8001", "--noreload"])
        self.assertIn("Error:", output.getvalue())

    @patch("hayavega.management.commands.serve", side_effect=KeyboardInterrupt)
    @patch("hayavega.management.commands.load_settings")
    def test_ctrl_c_during_serve_stops_cleanly(self, load_settings, serve):
        load_settings.return_value = SimpleNamespace(root_urlconf="test_project_urls", server_host="127.0.0.1", server_port=8000)
        urls = ModuleType("test_project_urls")
        urls.urlpatterns = []
        with patch.dict(sys.modules, {"test_project_urls": urls}):
            output = io.StringIO()
            with redirect_stdout(output):
                execute_from_command_line(["hayavega.py", "launch", "8001", "--noreload"])
        self.assertIn("development server stopped", output.getvalue())


class CacheserverFailureTests(unittest.TestCase):
    @patch("hayavega.management.commands.serve_cache", side_effect=KeyboardInterrupt)
    def test_ctrl_c_during_cacheserver_stops_cleanly(self, serve_cache):
        output = io.StringIO()
        with redirect_stdout(output):
            execute_from_command_line(["hayavega.py", "cacheserver"])
        self.assertIn("cache server stopped", output.getvalue())


if __name__ == "__main__":
    unittest.main()
