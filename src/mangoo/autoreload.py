"""Polling-based development autoreloader with no external dependencies."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def snapshot_python_files(root: Path) -> dict[Path, int]:
    """Return modification timestamps for project Python files."""
    return {path: path.stat().st_mtime_ns for path in root.rglob("*.py") if "__pycache__" not in path.parts}


def has_changes(previous: dict[Path, int], current: dict[Path, int]) -> bool:
    return previous != current


def run_with_reloader(command: list[str], root: Path | None = None, interval: float = 0.5) -> None:
    """Run a server child process and restart it when project code changes."""
    watch_root = (root or Path.cwd()).resolve()
    environment = os.environ.copy()
    environment["MANGOO_RUN_MAIN"] = "true"

    process = None
    try:
        while True:
            before = snapshot_python_files(watch_root)
            process = subprocess.Popen(command, env=environment, cwd=watch_root)
            while process.poll() is None:
                time.sleep(interval)
                if has_changes(before, snapshot_python_files(watch_root)):
                    print("Code change detected. Reloading Mangoo development server...")
                    process.terminate()
                    process.wait()
                    break
            else:
                return
    except KeyboardInterrupt:
        print("\nStopping Mangoo development server...")
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait()
