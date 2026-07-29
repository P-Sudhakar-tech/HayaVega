import os
import sys
from getpass import getpass

from mangoo.auth import UserAlreadyExistsError, create_superuser, enable_totp
from mangoo.autoreload import run_with_reloader
from mangoo.conf import load_settings
from mangoo.runtime import serve
from mangoo.hardening import HardeningOptions


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
    else:
        print("Commands: runserver [port] [--noreload], startapp <name>, createsuperuser, enable2fa <username>")


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
