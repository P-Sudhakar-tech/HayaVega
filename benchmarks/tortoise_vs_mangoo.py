"""HTTP-level benchmark: Tortoise ORM/`aiosqlite` (`benchmarks/tortoise_app`)
against Mangoo's own synchronous ORM under both transports, and against
FastAPI's own plain-`sqlite3` baseline (`benchmarks/fastapi_app`) -- the
"Tortoise ORM (or other async-driver) benchmark comparison" this document's
roadmap explicitly deferred as new scope, not forgotten: "It uses an async
driver (`aiosqlite`), a different shape of comparison than anything
`benchmarks/` currently runs (every existing target opens a plain
synchronous `sqlite3` connection per request, Mangoo's ORM included) --
building and validating that comparison fairly is new scope."

Scoped to the `db_list` scenario only (`GET /posts.json`), the one place an
async database driver can plausibly matter -- see `benchmarks/tortoise_app/app.py`'s
own module docstring for why (`aiosqlite` runs one dedicated background
thread per connection, not true async I/O, and that distinction is disclosed
there rather than assumed here).

Four targets, same `Post` shape and `SEED_COUNT` everywhere:
  - Tortoise ORM (FastAPI + aiosqlite)      -- the new target
  - FastAPI (sync sqlite3, uvicorn)         -- FastAPI's own non-Tortoise baseline
  - Mangoo (ASGI, sync ORM, uvicorn)        -- Mangoo's sync ORM dispatched to
                                                the ASGI sync-dispatch pool
  - Mangoo (WSGI, sync ORM, waitress)       -- Mangoo's sync ORM on its
                                                original transport

Run: python benchmarks/tortoise_vs_mangoo.py [--duration 10s] [--connections 30]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ScenarioResult:
    requests_per_sec: float
    p50_ms: float
    p90_ms: float
    p99_ms: float
    success_rate: float


def wait_until_ready(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/echo", timeout=1)
            return
        except Exception as error:  # noqa: BLE001 -- polling until the server accepts connections
            last_error = error
            time.sleep(0.2)
    raise RuntimeError(f"Server on port {port} never became ready: {last_error}")


def parse_oha_output(text: str) -> ScenarioResult:
    def find(pattern: str) -> float:
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f"Could not find {pattern!r} in oha output:\n{text}")
        return float(match.group(1))

    return ScenarioResult(
        requests_per_sec=find(r"Requests/sec:\s+([\d.]+)"),
        p50_ms=find(r"50\.00% in ([\d.]+) ms"),
        p90_ms=find(r"90\.00% in ([\d.]+) ms"),
        p99_ms=find(r"99\.00% in ([\d.]+) ms"),
        success_rate=find(r"Success rate:\s+([\d.]+)%"),
    )


def run_oha(url: str, *, duration: str, connections: int) -> ScenarioResult:
    # -u ms pins oha's reported unit; without it, oha auto-picks sec/ms/us
    # per result, which silently breaks the parser above whenever latencies
    # happen to cross a unit boundary between runs (see run.py).
    command = ["oha", "-z", duration, "-c", str(connections), "--no-tui", "-u", "ms", "-m", "GET", url]
    completed = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    return parse_oha_output(completed.stdout)


def benchmark_target(name: str, command: list[str], port: int, *, duration: str, connections: int) -> ScenarioResult:
    # `*_server.log` matches benchmarks/results/.gitignore's existing pattern
    # for per-run server logs -- RESULTS.md/README.md are the published
    # artifacts, these are not.
    log_path = ROOT / "benchmarks" / "results" / f"tortoise_vs_mangoo_{re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')}_server.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        try:
            wait_until_ready(port)
            run_oha(f"http://127.0.0.1:{port}/echo", duration="2s", connections=min(connections, 10))  # warm-up
            return run_oha(f"http://127.0.0.1:{port}/posts.json", duration=duration, connections=connections)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def build_targets() -> list[tuple[str, list[str], int]]:
    python = sys.executable
    return [
        (
            "Tortoise ORM (FastAPI + aiosqlite)",
            [python, "-m", "uvicorn", "benchmarks.tortoise_app.app:application", "--host", "127.0.0.1", "--port", "9011"],
            9011,
        ),
        (
            "FastAPI (sync sqlite3)",
            [python, "-m", "uvicorn", "benchmarks.fastapi_app.app:application", "--host", "127.0.0.1", "--port", "9002"],
            9002,
        ),
        (
            "Mangoo (ASGI, sync ORM)",
            [python, "-m", "uvicorn", "benchmarks.mangoo_asgi_app.app:application", "--host", "127.0.0.1", "--port", "9004"],
            9004,
        ),
        (
            "Mangoo (WSGI, sync ORM)",
            [python, "-m", "waitress", "--host=127.0.0.1", "--port=9001", "--threads=16", "--call", "benchmarks.mangoo_app.app:build_application"],
            9001,
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--connections", type=int, default=30)
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    if shutil.which("oha") is None:
        raise SystemExit("oha is not on PATH. See benchmarks/README.md for install instructions.")

    targets = build_targets()

    for run_index in range(1, args.runs + 1):
        print(f"=== Run {run_index}/{args.runs} (duration={args.duration}, concurrency={args.connections}) ===\n")
        results: dict[str, ScenarioResult] = {}
        for name, command, port in targets:
            print(f"--- {name} ---")
            try:
                result = benchmark_target(name, command, port, duration=args.duration, connections=args.connections)
                results[name] = result
                print(f"  db_list  {result.requests_per_sec:8.1f} req/s  p50={result.p50_ms:7.2f}ms  p90={result.p90_ms:7.2f}ms  p99={result.p99_ms:7.2f}ms  success={result.success_rate:.1f}%")
            except Exception as error:  # noqa: BLE001 -- report the failure and keep going with the other targets
                print(f"  FAILED: {error}")
            print()

        if "Tortoise ORM (FastAPI + aiosqlite)" in results:
            tortoise = results["Tortoise ORM (FastAPI + aiosqlite)"]
            print("--- Summary (ratio = target / Tortoise ORM) ---")
            for name, result in results.items():
                if name == "Tortoise ORM (FastAPI + aiosqlite)":
                    continue
                ratio = result.requests_per_sec / tortoise.requests_per_sec
                print(f"  {name:38s} {result.requests_per_sec:8.1f} req/s  ratio={ratio:.2f}x")
            print()


if __name__ == "__main__":
    main()
