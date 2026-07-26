import argparse
import os
from pathlib import Path

from .scaffold import startapp, startproject


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mango")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("startproject", "startapp"):
        subparser = commands.add_parser(command)
        subparser.add_argument("name")
    launch = commands.add_parser("launch", help="Start the development server.")
    launch.add_argument("port", type=int, nargs="?", default=8000)
    launch.add_argument("--noreload", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "launch":
        from .management.commands import execute_from_command_line

        os.environ["MANGOO_LAUNCH_MODE"] = "module"
        command_args = ["mango", "runserver", str(args.port)]
        if args.noreload:
            command_args.append("--noreload")
        execute_from_command_line(command_args)
        return

    action = startproject if args.command == "startproject" else startapp
    created = action(args.name, Path.cwd())
    print(f"Created {args.command}: {created}")


if __name__ == "__main__":
    main()
