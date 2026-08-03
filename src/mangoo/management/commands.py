import os
import sys
from getpass import getpass

from mangoo.auth import UserAlreadyExistsError, create_superuser, enable_totp
from mangoo.autoreload import run_with_reloader
from mangoo.conf import load_settings
from mangoo.runtime import serve
from mangoo.hardening import HardeningOptions
from mangoo.migrations import MigrationGenerationError, apply as apply_migrations, generate as generate_migration, status as migration_status
from mangoo.production import ProductionConfigurationError, validate_production


def execute_from_command_line(argv: list[str] | None = None) -> None:
    args = argv or sys.argv
    command = args[1] if len(args) > 1 else "help"
    if command == "runserver":
        _runserver(args)
    elif command == "startapp" and len(args) == 3:
        from mangoo.scaffold import startapp
        startapp(args[2])
    elif command == "createsuperuser":
        _create_superuser(args)
    elif command == "enable2fa" and len(args) == 3:
        _enable2fa(args[2])
    elif command == "migrate":
        _migrate()
    elif command == "showmigrations":
        _showmigrations()
    elif command == "makemigrations" and len(args) == 3:
        _makemigrations(args[2])
    else:
        print("Commands: runserver [port] [--noreload], startapp <name>, createsuperuser, enable2fa <username>, makemigrations <app>, migrate, showmigrations")


def _runserver(args: list[str]) -> None:
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
    try:
        serve(urls.urlpatterns, host, int(port or 8000), hardening=HardeningOptions.from_settings(allowed_hosts=getattr(settings, "allowed_hosts", ()), security=getattr(settings, "security", {})), debug=getattr(settings, "debug", False))
    except KeyboardInterrupt:
        print("\nMangoo development server stopped.")


def _create_superuser(args: list[str], prompt=input, password_prompt=getpass) -> None:
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


def _migration_directories(settings) -> list:
    directories = []
    for app in settings.installed_apps:
        module = __import__(app, fromlist=["__file__"])
        if getattr(module, "__file__", None):
            directories.append(os.path.join(os.path.dirname(module.__file__), "migrations"))
    return directories


def _migrate() -> None:
    settings = load_settings()
    applied = apply_migrations(settings.database, _migration_directories(settings))
    print("Applied: " + ", ".join(applied) if applied else "No migrations to apply.")


def _showmigrations() -> None:
    settings = load_settings()
    records = migration_status(settings.database, _migration_directories(settings))
    if not records:
        print("No migrations found.")
        return
    for record in records:
        print(f"[{'X' if record.applied else ' '}] {record.name}")


def _makemigrations(app: str) -> None:
    settings = load_settings()
    if app not in settings.installed_apps:
        print(f"Error: {app!r} is not in INSTALLED_APPS.")
        return
    module = __import__(f"{app}.models", fromlist=["__file__"])
    app_module = __import__(app, fromlist=["__file__"])
    try:
        generated = generate_migration(module, os.path.join(os.path.dirname(app_module.__file__), "migrations"), settings.database["ENGINE"])
    except MigrationGenerationError as error:
        print(f"Error: {error}")
        return
    print(f"Created {generated}" if generated else "No entities found; no migration created.")
