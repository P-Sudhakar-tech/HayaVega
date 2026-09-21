"""HTTP-level benchmark for the one ASGI scenario every prior comparison in
this suite explicitly left open: real concurrent I/O. `run.py` and
`rust_runtime_vs_fastapi.py`'s scenarios (`json_echo`, `db_list`,
`template_render`, `static_file`) are all plain sync functions under every
transport, including Mangoo (ASGI) -- so none of them exercise the one
property ASGI actually has that WSGI doesn't: an `async def` view awaiting
I/O yields the event loop instead of blocking a whole OS thread for the
call's duration, so many more requests can be in flight at once than there
are threads.

This benchmarks that directly, against a controlled stand-in "upstream"
service (`benchmarks/upstream_app`, a raw ASGI app that just sleeps for a
fixed delay -- simulating a real network call without real-network
flakiness) instead of a live third party, so the result is reproducible.

Three comparisons, same upstream, same delay, run back to back:

1. **Mangoo (ASGI) `/remote` vs. FastAPI `/remote`** -- both real `async def`
   views awaiting a network call, both served by uvicorn. The comparison
   this file exists to answer.
2. **Mangoo (ASGI) `/remote` vs. Mangoo (ASGI) `/remote-blocking`** -- same
   process, same routing code, same server; only whether the view awaits
   the call or blocks a thread for it differs. Isolates ASGI's own claimed
   benefit from any cross-framework difference.
3. **Mangoo (WSGI/waitress) `/remote-blocking` vs. FastAPI `/remote-blocking`**
   -- both a blocking `def` view dispatched through a fixed-size thread
   pool (waitress's 16 threads; Starlette/anyio's default capacity
   limiter), the baseline neither framework's async path is compared
   against in isolation elsewhere in this suite.

Concurrency defaults to 64 -- deliberately above every blocking path's
thread-pool size in this comparison (Mangoo ASGI's dispatch pool and
waitress both default to 16; see `mangoo.asgi`'s `max_sync_workers`
docstring) so a real queuing ceiling has room to show up if the async paths
avoid it and the blocking ones don't.

The async views use `benchmarks/_upstream_client.py`, not
`httpx.AsyncClient` -- see that module's docstring for two real,
disclosed bugs found while building this benchmark (httpx itself not
overlapping concurrent requests on this dev environment, then a
connection-per-request client reproducing this project's own previously-
diagnosed Windows ephemeral-port exhaustion). Each target below also gets
its own freshly started upstream process, not one shared across all
three sequentially -- an earlier version shared one and a target killed
mid-connection left `CLOSE_WAIT` sockets that silently penalized
whichever target ran next; see `benchmark_target`'s docstring comment.

Run: python benchmarks/asgi_concurrent_io.py [--duration 10s] [--connections 64] [--delay-ms 40]
"""

from __future__ import annotations

import argparse
import os
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


def wait_until_ready(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception as error:  # noqa: BLE001 -- polling until the server accepts connections
            last_error = error
            time.sleep(0.2)
    raise RuntimeError(f"Server at {url} never became ready: {last_error}")


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
    completed = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    return parse_oha_output(completed.stdout)


@dataclass
class Target:
    name: str
    command: list[str]
    port: int
    paths: dict[str, str]  # scenario label -> URL path


def _start(command: list[str], ready_url: str, *, env: dict[str, str] | None = None) -> subprocess.Popen:
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_until_ready(ready_url)
    except Exception:
        process.terminate()
        process.wait(timeout=5)
        raise
    return process


def _stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def benchmark_target(target: Target, *, duration: str, connections: int, upstream_env: dict[str, str]) -> dict[str, ScenarioResult]:
    # A fresh upstream per target, not one long-lived process shared across
    # all three -- sharing one was tried first and rejected after a real
    # diagnosis: killing target N's process with SIGTERM doesn't give its
    # outbound connections to the upstream a clean FIN handshake, so the
    # upstream accumulates CLOSE_WAIT sockets that were still there,
    # unfairly, when target N+1's benchmark began. `netstat` confirmed 64
    # CLOSE_WAIT + 64 FIN_WAIT_2 entries after repeated runs against a
    # shared upstream, and results on the same ports were reproducibly ~5x
    # worse than an identical target measured against a clean upstream on
    # an unused port -- not noise (see benchmarks/README.md for the full
    # trail, including the raw-socket and httpx dead ends investigated
    # before this was found). Restarting the upstream per target costs a
    # few hundred ms of startup time and removes the whole class of
    # cross-target contamination.
    upstream = _start(
        [sys.executable, "-m", "uvicorn", "benchmarks.upstream_app.app:application", "--host", "127.0.0.1", "--port", "9010"],
        "http://127.0.0.1:9010/delay",
        env=upstream_env,
    )
    try:
        base_url = f"http://127.0.0.1:{target.port}"
        process = _start(target.command, f"{base_url}/{next(iter(target.paths.values())).lstrip('/')}")
        try:
            results = {}
            for scenario_name, scenario_path in target.paths.items():
                url = f"{base_url}{scenario_path}"
                run_oha(url, duration="2s", connections=min(connections, 10))  # warm-up
                results[scenario_name] = run_oha(url, duration=duration, connections=connections)
            return results
        finally:
            _stop(process)
    finally:
        _stop(upstream)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--connections", type=int, default=64)
    parser.add_argument("--delay-ms", type=int, default=40, help="Artificial upstream latency (default: 40ms)")
    args = parser.parse_args()

    if shutil.which("oha") is None:
        raise SystemExit("oha is not on PATH. See benchmarks/README.md for install instructions.")

    python = sys.executable
    upstream_env = {**os.environ, "MANGOO_BENCH_UPSTREAM_DELAY_MS": str(args.delay_ms)}

    targets = [
        Target(
            "Mangoo (ASGI)",
            [python, "-m", "uvicorn", "benchmarks.mangoo_asgi_app.app:application", "--host", "127.0.0.1", "--port", "9004"],
            9004,
            {"remote (async)": "/remote", "remote-blocking (16-thread pool)": "/remote-blocking"},
        ),
        Target(
            "FastAPI (uvicorn)",
            [python, "-m", "uvicorn", "benchmarks.fastapi_app.app:application", "--host", "127.0.0.1", "--port", "9002"],
            9002,
            {"remote (async)": "/remote", "remote-blocking (anyio pool)": "/remote-blocking"},
        ),
        Target(
            "Mangoo (WSGI/waitress)",
            [python, "-m", "waitress", "--host=127.0.0.1", "--port=9001", "--threads=16", "--call", "benchmarks.mangoo_app.app:build_application"],
            9001,
            {"remote-blocking (16-thread pool)": "/remote-blocking"},
        ),
    ]

    print(f"Benchmark: real concurrent I/O, ASGI vs FastAPI vs WSGI (duration={args.duration}, concurrency={args.connections}, upstream delay={args.delay_ms}ms)\n")
    all_results: dict[str, dict[str, ScenarioResult]] = {}
    for target in targets:
        print(f"--- {target.name} ---")
        try:
            all_results[target.name] = benchmark_target(target, duration=args.duration, connections=args.connections, upstream_env=upstream_env)
            for scenario_name, result in all_results[target.name].items():
                print(f"  {scenario_name:32s} {result.requests_per_sec:8.1f} req/s  p50={result.p50_ms:7.2f}ms  p90={result.p90_ms:7.2f}ms  p99={result.p99_ms:7.2f}ms  success={result.success_rate:.1f}%")
        except Exception as error:  # noqa: BLE001 -- report the failure and keep going with the other targets
            print(f"  FAILED: {error}")
        print()

    mangoo_asgi = all_results.get("Mangoo (ASGI)", {})
    fastapi_results = all_results.get("FastAPI (uvicorn)", {})
    mangoo_wsgi = all_results.get("Mangoo (WSGI/waitress)", {})

    print("--- Summary ---")
    if "remote (async)" in mangoo_asgi and "remote (async)" in fastapi_results:
        a, f = mangoo_asgi["remote (async)"], fastapi_results["remote (async)"]
        print(f"  1. Mangoo (ASGI) vs FastAPI, both async:      {a.requests_per_sec:8.1f} vs {f.requests_per_sec:8.1f} req/s  ratio={a.requests_per_sec / f.requests_per_sec:.2f}x")
    if "remote (async)" in mangoo_asgi and "remote-blocking (16-thread pool)" in mangoo_asgi:
        a, b = mangoo_asgi["remote (async)"], mangoo_asgi["remote-blocking (16-thread pool)"]
        print(f"  2. Mangoo (ASGI) async vs its own blocking:   {a.requests_per_sec:8.1f} vs {b.requests_per_sec:8.1f} req/s  ratio={a.requests_per_sec / b.requests_per_sec:.2f}x")
    if "remote-blocking (16-thread pool)" in mangoo_wsgi and "remote-blocking (anyio pool)" in fastapi_results:
        w, f = mangoo_wsgi["remote-blocking (16-thread pool)"], fastapi_results["remote-blocking (anyio pool)"]
        print(f"  3. Mangoo (WSGI) vs FastAPI, both blocking:   {w.requests_per_sec:8.1f} vs {f.requests_per_sec:8.1f} req/s  ratio={w.requests_per_sec / f.requests_per_sec:.2f}x")


if __name__ == "__main__":
    main()
