import os
import sys
from getpass import getpass

from mangoo.auth import UserAlreadyExistsError, create_superuser, enable_totp
from mangoo.autoreload import run_with_reloader
from mangoo.checks import run_checks
from mangoo.conf import load_settings
from mangoo.hardening import HardeningOptions
from mangoo.jobs import run_worker
from mangoo.migrations import MigrationGenerationError, migration_directories
from mangoo.migrations import apply as apply_migrations
from mangoo.migrations import generate as generate_migration
from mangoo.migrations import status as lookup_migration_status
from mangoo.production import ProductionConfigurationError, validate_production
from mangoo.runtime import serve
from mangoo.rust_runtime import RustRuntimeUnavailableError
from mangoo.rust_runtime import serve as serve_rust

HELP_TEXT = "Commands: launch [port] [--noreload], newapp <name>, createadmin, enable2fa <username>, newmigration <app>, applymigrations, migrationstatus, diagnose, runworker"

# Django spells these runserver/startapp/createsuperuser/migrate/
# makemigrations/showmigrations; Mangoo's own names below are distinct on
# purpose. The Django names still work, unchanged, as deprecated aliases
# rather than a hard break -- the same policy this project already applies
# to `mangoo.addons.pulse`/`pulse=`.
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
        from mangoo.scaffold import startapp

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
    else:
        print(HELP_TEXT)


def _launch(args: list[str]) -> None:
    use_reloader = "--noreload" not in args
    if use_reloader and os.environ.get("MANGOO_RUN_MAIN") != "true":
        if os.environ.get("MANGOO_LAUNCH_MODE") == "module":
            command = [sys.executable, "-m", "mangoo.cli", "launch", *args[2:]]
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
        print("\nMangoo development server stopped.")


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
        user = create_superuser(settings.database, username, email, password)
    except (UserAlreadyExistsError, ValueError) as error:
        print(f"Error: {error}")
        return
    print(f"Superuser '{user.username}' created successfully.")


def _enable2fa(username: str) -> None:
    try:
        secret = enable_totp(load_settings().database, username)
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
        print(f"Error: {app!r} is not in INSTALLED_APPS.")
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
    print(f"Mangoo job worker started, {len(handlers)} handler(s) registered.")
    try:
        run_worker(settings.database, handlers)
    except KeyboardInterrupt:
        print("\nMangoo job worker stopped.")


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
