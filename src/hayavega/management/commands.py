import argparse
import os
import sys
from getpass import getpass

from hayavega.auth import UserAlreadyExistsError, create_superuser, enable_totp
from hayavega.autoreload import run_with_reloader
from hayavega.cacheserver import serve as serve_cache
from hayavega.checks import run_checks
from hayavega.conf import load_settings
from hayavega.hardening import HardeningOptions
from hayavega.jobs import run_worker
from hayavega.migrations import MigrationGenerationError, migration_directories
from hayavega.migrations import apply as apply_migrations
from hayavega.migrations import generate as generate_migration
from hayavega.migrations import status as lookup_migration_status
from hayavega.production import ProductionConfigurationError, validate_production
from hayavega.runtime import serve
from hayavega.rust_runtime import RustRuntimeUnavailableError
from hayavega.rust_runtime import serve as serve_rust

HELP_TEXT = "Commands: launch [port] [--noreload], newapp <name>, createadmin, enable2fa <username>, newmigration <app>, applymigrations, migrationstatus, diagnose, runworker, cacheserver [--host H] [--port P] [--password PW]"

# Django spells these runserver/startapp/createsuperuser/migrate/
# makemigrations/showmigrations; Hayavega's own names below are distinct on
# purpose. The Django names still work, unchanged, as deprecated aliases
# rather than a hard break -- the same policy this project already applies
# to `hayavega.addons.pulse`/`pulse=`.
_DEPRECATED_ALIASES = {
    "runserver": "launch",
    "startapp": "newapp",
    "createsuperuser": "createadmin",
    "migrate": "applymigrations",
    "makemigrations": "newmigration",
    "showmigrations": "migrationstatus",
}


def execute_from_command_line(argv: list[str] | None = None) -> None:
    args = argv or sys.argv
    command = args[1] if len(args) > 1 else "help"
    canonical = _DEPRECATED_ALIASES.get(command, command)
    if canonical != command:
        print(f"Warning: '{command}' is deprecated; use '{canonical}' instead.", file=sys.stderr)
    if canonical == "launch":
        _launch(args)
    elif canonical == "newapp" and len(args) == 3:
        from hayavega.scaffold import startapp

        startapp(args[2])
    elif canonical == "createadmin":
        _createadmin(args)
    elif canonical == "enable2fa" and len(args) == 3:
        _enable2fa(args[2])
    elif canonical == "applymigrations":
        _applymigrations()
    elif canonical == "migrationstatus":
        _migrationstatus()
    elif canonical == "newmigration" and len(args) == 3:
        _newmigration(args[2])
    elif canonical == "diagnose":
        _diagnose()
    elif canonical == "runworker":
        _runworker()
    elif canonical == "cacheserver":
        _cacheserver(args[2:])
    elif not _dispatch_custom_command(canonical, args[2:]):
        print(HELP_TEXT)


def _launch(args: list[str]) -> None:
    use_reloader = "--noreload" not in args
    if use_reloader and os.environ.get("HAYAVEGA_RUN_MAIN") != "true":
        if os.environ.get("HAYAVEGA_LAUNCH_MODE") == "module":
            command = [sys.executable, "-m", "hayavega.cli", "launch", *args[2:]]
        else:
            command = [sys.executable, sys.argv[0], *args[1:]]
        run_with_reloader(command)
        return

    settings = load_settings()
    try:
        validate_production(settings)
    except ProductionConfigurationError as error:
        print(f"Error: {error}")
        return
    urls = __import__(settings.root_urlconf, fromlist=["urlpatterns"])
    address_arguments = [argument for argument in args[2:] if argument != "--noreload"]
    address = address_arguments[0] if address_arguments else f"{settings.server_host}:{settings.server_port}"
    if ":" in address:
        host, _, port = address.partition(":")
    else:
        host, port = "127.0.0.1", address
    hardening = HardeningOptions.from_settings(allowed_hosts=getattr(settings, "allowed_hosts", ()), security=getattr(settings, "security", {}))
    debug = getattr(settings, "debug", False)
    server_engine = getattr(settings, "server_engine", "python")
    try:
        if server_engine == "rust":
            serve_rust(urls.urlpatterns, host, int(port or 8000), hardening=hardening, debug=debug, tls_cert=getattr(settings, "server_tls_cert", None), tls_key=getattr(settings, "server_tls_key", None))
        else:
            serve(urls.urlpatterns, host, int(port or 8000), hardening=hardening, debug=debug)
    except RustRuntimeUnavailableError as error:
        print(f"Error: {error}")
    except KeyboardInterrupt:
        print("\nHayavega development server stopped.")


def _load_password_validators(dotted_paths):
    """Resolve `Settings.password_validators` the same dotted-path way
    `job_handlers` already is in `_runworker` -- empty means "use the
    default `validate_password`" (`create_superuser`'s own default)."""
    validators = []
    for dotted_path in dotted_paths:
        module_path, _, attribute = dotted_path.rpartition(".")
        module = __import__(module_path, fromlist=[attribute])
        validators.append(getattr(module, attribute))
    return validators or None


def _createadmin(args: list[str], prompt=input, password_prompt=getpass) -> None:
    settings = load_settings()
    username = prompt("Username: ").strip()
    email = prompt("Email address: ").strip()
    password = password_prompt("Password: ")
    confirmation = password_prompt("Password (again): ")
    if password != confirmation:
        print("Error: the passwords do not match.")
        return
    try:
        user = create_superuser(settings.database, username, email, password, validators=_load_password_validators(getattr(settings, "password_validators", ())))
    except (UserAlreadyExistsError, ValueError) as error:
        print(f"Error: {error}")
        return
    print(f"Superuser '{user.username}' created successfully.")


def _enable2fa(username: str) -> None:
    settings = load_settings()
    try:
        secret = enable_totp(settings.database, username, secret_key=settings.secret_key)
    except ValueError as error:
        print(f"Error: {error}")
        return
    print(f"Two-factor authentication enabled for '{username}'.")
    print(f"Add this secret to an authenticator app: {secret}")


def _applymigrations() -> None:
    settings = load_settings()
    applied = apply_migrations(settings.database, migration_directories(settings))
    print("Applied: " + ", ".join(applied) if applied else "No migrations to apply.")


def _migrationstatus() -> None:
    settings = load_settings()
    records = lookup_migration_status(settings.database, migration_directories(settings))
    if not records:
        print("No migrations found.")
        return
    for record in records:
        print(f"[{'X' if record.applied else ' '}] {record.name}")


def _newmigration(app: str) -> None:
    settings = load_settings()
    if app not in settings.installed_apps:
        print(f"Error: {app!r} is not in Settings.installed_apps.")
        return
    module = __import__(f"{app}.models", fromlist=["__file__"])
    app_module = __import__(app, fromlist=["__file__"])
    app_module_file = getattr(app_module, "__file__", None)
    if not app_module_file:
        print(f"Error: {app!r} has no discoverable file path; cannot locate its migrations directory.")
        return
    try:
        generated = generate_migration(module, os.path.join(os.path.dirname(app_module_file), "migrations"), settings.database["ENGINE"])
    except MigrationGenerationError as error:
        print(f"Error: {error}")
        return
    print(f"Created {generated}" if generated else "No entities found; no migration created.")


def _runworker() -> None:
    settings = load_settings()
    handlers = {}
    for name, dotted_path in settings.job_handlers.items():
        module_path, _, attribute = dotted_path.rpartition(".")
        module = __import__(module_path, fromlist=[attribute])
        handlers[name] = getattr(module, attribute)
    print(f"Hayavega job worker started, {len(handlers)} handler(s) registered.")
    try:
        run_worker(settings.database, handlers)
    except KeyboardInterrupt:
        print("\nHayavega job worker stopped.")


def _cacheserver(args: list[str]) -> None:
    """Runs standalone -- no project, no `Settings`, needed: `hayavega.py
    cacheserver` (via this function) and the installed `hayavega cacheserver`
    (via `cli.py`'s own subparser) both reach this one implementation."""
    parser = argparse.ArgumentParser(prog="hayavega cacheserver", add_help=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--password", default=None)
    parsed = parser.parse_args(args)
    try:
        serve_cache(parsed.host, parsed.port, password=parsed.password)
    except ValueError as error:
        print(f"Error: {error}")
    except KeyboardInterrupt:
        print("\nHayavega cache server stopped.")


def _dispatch_custom_command(name: str, extra_args: list[str]) -> bool:
    """Check `Settings.commands` (dotted paths, resolved the same way
    `job_handlers` already is in `_runworker`) for a project-registered
    command named `name`, and run it if found.

    Returns `False` for an unrecognized name *or* when settings can't
    even be loaded (e.g. `hayavega.py` invoked outside a real project) --
    the caller falls through to `HELP_TEXT` either way, so a project
    with no `config.settings` module never sees an unrelated import
    error where it previously just saw the help text.
    """
    try:
        settings = load_settings()
    except Exception:
        return False
    dotted_path = settings.commands.get(name)
    if dotted_path is None:
        return False
    module_path, _, attribute = dotted_path.rpartition(".")
    module = __import__(module_path, fromlist=[attribute])
    handler = getattr(module, attribute)
    handler(extra_args)
    return True


def _diagnose() -> None:
    settings = load_settings()
    messages = run_checks(settings)
    if not messages:
        print("No issues found.")
        return
    for message in messages:
        print(message)
    errors = sum(1 for message in messages if message.level == "error")
    warnings_count = sum(1 for message in messages if message.level == "warning")
    print(f"\n{errors} error(s), {warnings_count} warning(s).")
