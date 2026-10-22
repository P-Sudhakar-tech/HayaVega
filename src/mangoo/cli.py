import argparse
import os
import sys
from pathlib import Path

from .scaffold import startapp, startproject

# Django spells these `startproject`/`startapp`; Mangoo's own commands are
# `newproject`/`newapp`. The Django names still work, unchanged, as
# deprecated aliases rather than a hard break -- the same policy this
# project already applies to `mangoo.addons.pulse`/`pulse=`.
_SCAFFOLD_COMMANDS = {"newproject": startproject, "newapp": startapp}
_DEPRECATED_ALIASES = {"startproject": "newproject", "startapp": "newapp"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mango")
    commands = parser.add_subparsers(dest="command", required=True)
    for command, alias in _DEPRECATED_ALIASES.items():
        subparser = commands.add_parser(alias, aliases=[command])
        subparser.add_argument("name")
    launch = commands.add_parser("launch", help="Start the development server.")
    launch.add_argument("port", type=int, nargs="?", default=8000)
    launch.add_argument("--noreload", action="store_true")
    commands.add_parser("diagnose", help="Report unsafe settings, deprecated API usage, and pending migrations.")
    cacheserver = commands.add_parser("cacheserver", help="Run the built-in networked cache server -- a drop-in Redis substitute for mangoo.cache.RedisCache.")
    cacheserver.add_argument("--host", default="127.0.0.1")
    cacheserver.add_argument("--port", type=int, default=6379)
    cacheserver.add_argument("--password", default=None)
    args = parser.parse_args(argv)

    if args.command in _DEPRECATED_ALIASES:
        print(f"Warning: 'mango {args.command}' is deprecated; use 'mango {_DEPRECATED_ALIASES[args.command]}' instead.", file=sys.stderr)

    if args.command == "launch":
        from .management.commands import execute_from_command_line

        os.environ["MANGOO_LAUNCH_MODE"] = "module"
        command_args = ["mango", "launch", str(args.port)]
        if args.noreload:
            command_args.append("--noreload")
        execute_from_command_line(command_args)
        return

    if args.command == "diagnose":
        from .management.commands import execute_from_command_line

        execute_from_command_line(["mango", "diagnose"])
        return

    if args.command == "cacheserver":
        from .management.commands import execute_from_command_line

        command_args = ["mango", "cacheserver", "--host", args.host, "--port", str(args.port)]
        if args.password is not None:
            command_args += ["--password", args.password]
        execute_from_command_line(command_args)
        return

    canonical = _DEPRECATED_ALIASES.get(args.command, args.command)
    created = _SCAFFOLD_COMMANDS[canonical](args.name, Path.cwd())
    print(f"Created {canonical}: {created}")


if __name__ == "__main__":
    main()
