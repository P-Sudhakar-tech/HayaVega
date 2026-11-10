"""Cold-start benchmark: wall-clock time from process launch to the server
accepting its first successful request. Not measured anywhere else in this
suite -- every other benchmark here measures steady-state throughput of an
already-running, already-warmed-up server. A framework that's fast once
warm but slow to boot matters for serverless/container cold starts, CLI
dev-server restarts, and autoreload cycles, none of which the oha-driven
benchmarks above touch at all.

Measures: process spawn -> first successful GET /echo, for every target
this suite already runs (same apps, same ports), five trials each,
sequential (never two servers racing for the same CPU at once).

Run: python benchmarks/startup_time.py [--trials 5]
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def wait_until_ready(port: int, timeout: float = 15.0) -> float:
    start = time.perf_counter()
    deadline = start + timeout
    last_error = None
    while time.perf_counter() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/echo", timeout=1)
            return time.perf_counter() - start
        except Exception as error:  # noqa: BLE001 -- polling until the server accepts connections
            last_error = error
            time.sleep(0.01)
    raise RuntimeError(f"Server on port {port} never became ready: {last_error}")


def time_one_boot(command: list[str], port: int) -> float:
    t0 = time.perf_counter()
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_until_ready(port)
        return time.perf_counter() - t0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()

    python = sys.executable
    targets = [
        ("Hayavega (waitress)", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9001", "--threads=16", "--call", "benchmarks.hayavega_app.app:build_application"], 9001),
        ("Hayavega (Rust runtime)", [python, str(ROOT / "benchmarks" / "_rust_runtime_target.py")], 9006),
        ("Hayavega (ASGI/uvicorn)", [python, "-m", "uvicorn", "benchmarks.hayavega_asgi_app.app:application", "--host", "127.0.0.1", "--port", "9004"], 9004),
        ("FastAPI (uvicorn)", [python, "-m", "uvicorn", "benchmarks.fastapi_app.app:application", "--host", "127.0.0.1", "--port", "9002"], 9002),
        ("Django (waitress)", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9003", "--threads=16", "benchmarks.django_app.app:application"], 9003),
    ]

    print(f"Cold-start benchmark: process launch -> first successful GET /echo ({args.trials} trials each)\n")
    summary = []
    for name, command, port in targets:
        samples = []
        for trial in range(args.trials):
            try:
                elapsed = time_one_boot(command, port)
                samples.append(elapsed)
                print(f"  {name:28s} trial {trial + 1}: {elapsed * 1000:7.1f} ms")
            except Exception as error:  # noqa: BLE001 -- report and keep going with remaining trials/targets
                print(f"  {name:28s} trial {trial + 1}: FAILED ({error})")
        if samples:
            summary.append((name, samples))
        print()

    print("--- Summary (median of successful trials) ---")
    for name, samples in summary:
        median_ms = statistics.median(samples) * 1000
        print(f"  {name:28s} median={median_ms:8.1f} ms  min={min(samples) * 1000:7.1f} ms  max={max(samples) * 1000:7.1f} ms  n={len(samples)}")

    if len(summary) >= 2:
        fastest = min(summary, key=lambda item: statistics.median(item[1]))
        print(f"\nFastest to boot: {fastest[0]} (median {statistics.median(fastest[1]) * 1000:.1f} ms)")
        for name, samples in summary:
            if name != fastest[0]:
                ratio = statistics.median(samples) / statistics.median(fastest[1])
                print(f"  {fastest[0]} boots {ratio:.2f}x faster than {name}")


if __name__ == "__main__":
    main()
