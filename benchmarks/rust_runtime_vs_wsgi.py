"""HTTP-level benchmark: `mangoo.rust_runtime` (the new native Rust WSGI
server, `SERVER['ENGINE'] = 'rust'`) vs. the existing Python runtime served
by `waitress` -- the "with and without" comparison for the custom Rust HTTP
runtime work. Same app (`benchmarks/mangoo_app/app.py`), same routes, same
seeded data; only the server differs.

Scope note (see `src/mangoo/rust_runtime.py` and
`crates/mangoo-python/src/server.rs` for the full disclosure): this is a
synchronous, fixed-thread-pool Rust server with no HTTP keep-alive yet, not
an async `tokio`/`hyper` runtime -- CPython's GIL means async I/O would not
let more Python view code run concurrently than a thread pool already does,
so the comparison here is specifically "does moving the socket-accept/
HTTP-parsing layer to Rust help," not "does async help."

Run: python benchmarks/rust_runtime_vs_wsgi.py [--duration 10s] [--connections 30]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCENARIOS = [
    ("json_echo", "/echo"),
    ("db_list", "/posts.json"),
    ("template_render", "/posts"),
    ("static_file", "/static/logo.svg"),
]


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
    command = ["oha", "-z", duration, "-c", str(connections), "--no-tui", "-u", "ms", "-m", "GET", url]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return parse_oha_output(completed.stdout)


def benchmark_target(name: str, command: list[str], port: int, *, duration: str, connections: int) -> dict[str, ScenarioResult]:
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_until_ready(port)
        run_oha(f"http://127.0.0.1:{port}/echo", duration="2s", connections=min(connections, 10))  # warm-up
        results = {}
        for scenario_name, scenario_path in SCENARIOS:
            results[scenario_name] = run_oha(f"http://127.0.0.1:{port}{scenario_path}", duration=duration, connections=connections)
        return results
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--connections", type=int, default=30)
    args = parser.parse_args()

    import shutil

    if shutil.which("oha") is None:
        raise SystemExit("oha is not on PATH. See benchmarks/README.md for install instructions.")

    python = sys.executable
    targets = [
        ("Rust runtime", [python, str(ROOT / "benchmarks" / "_rust_runtime_target.py")], 9006),
        ("Python runtime (waitress)", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9007", "--threads=16", "--call", "benchmarks.mangoo_app.app:build_application"], 9007),
    ]

    print(f"Benchmark: mangoo.rust_runtime vs waitress (duration={args.duration}, concurrency={args.connections})\n")
    all_results: dict[str, dict[str, ScenarioResult]] = {}
    for name, command, port in targets:
        print(f"--- {name} ---")
        try:
            all_results[name] = benchmark_target(name, command, port, duration=args.duration, connections=args.connections)
            for scenario_name, result in all_results[name].items():
                print(f"  {scenario_name:18s} {result.requests_per_sec:8.1f} req/s  p50={result.p50_ms:7.2f}ms  p90={result.p90_ms:7.2f}ms  p99={result.p99_ms:7.2f}ms  success={result.success_rate:.1f}%")
        except Exception as error:  # noqa: BLE001 -- report the failure and keep going with the other target
            print(f"  FAILED: {error}")
        print()

    if len(all_results) == 2:
        print("--- Summary (Rust runtime vs waitress) ---")
        rust = all_results.get("Rust runtime", {})
        python_results = all_results.get("Python runtime (waitress)", {})
        for scenario_name, _ in SCENARIOS:
            if scenario_name in rust and scenario_name in python_results:
                ratio = rust[scenario_name].requests_per_sec / python_results[scenario_name].requests_per_sec
                print(f"  {scenario_name:18s} rust={rust[scenario_name].requests_per_sec:8.1f} req/s  waitress={python_results[scenario_name].requests_per_sec:8.1f} req/s  ratio={ratio:.2f}x")


if __name__ == "__main__":
    main()
