"""Benchmark orchestration: starts each app behind a production-realistic
server, runs `oha` against each scenario, and writes benchmarks/results/RESULTS.md.

Requires: oha (https://github.com/hatoo/oha) on PATH, and this repo's [dev]
extras plus fastapi/uvicorn/jinja2/django installed (see benchmarks/README.md).

Usage:
    python benchmarks/run.py [--duration 10s] [--connections 50] [--requests-warmup 50]
"""

from __future__ import annotations

import argparse
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).resolve().parent / "results"

SCENARIOS = [
    ("json_echo", "GET", "/echo"),
    ("db_list", "GET", "/posts.json"),
    ("template_render", "GET", "/posts"),
    ("static_file", "GET", "/static/logo.svg"),
]


@dataclass
class Target:
    name: str
    port: int
    command: list[str]
    server: str


@dataclass
class ScenarioResult:
    requests_per_sec: float
    p50_ms: float
    p90_ms: float
    p99_ms: float
    success_rate: float


@dataclass
class TargetResult:
    target: Target
    scenarios: dict[str, list[ScenarioResult]] = field(default_factory=dict)
    error: str | None = None


@dataclass
class AggregatedResult:
    requests_per_sec_median: float
    requests_per_sec_min: float
    requests_per_sec_max: float
    p50_ms_median: float
    success_rate_min: float


def aggregate(results: list[ScenarioResult]) -> AggregatedResult:
    requests_per_sec = [r.requests_per_sec for r in results]
    p50_ms = [r.p50_ms for r in results]
    success_rate = [r.success_rate for r in results]
    return AggregatedResult(
        requests_per_sec_median=statistics.median(requests_per_sec),
        requests_per_sec_min=min(requests_per_sec),
        requests_per_sec_max=max(requests_per_sec),
        p50_ms_median=statistics.median(p50_ms),
        success_rate_min=min(success_rate),
    )


def build_targets(connections: int = 50) -> list[Target]:
    python = sys.executable
    # waitress refuses new connections past --connection-limit (default 100)
    # and queues the rest instead of serving them -- with the benchmark's
    # own --connections exceeding that default, every waitress-backed target
    # would otherwise queue most of its load for the whole run and report
    # "aborted due to deadline" on nearly everything, not a real performance
    # result. Size the limit to the actual concurrency being driven.
    connection_limit = max(connections, 100)
    return [
        Target(
            "Hayavega",
            9001,
            [python, "-m", "waitress", "--host=127.0.0.1", "--port=9001", "--threads=16", f"--connection-limit={connection_limit}", "--call", "benchmarks.hayavega_app.app:build_application"],
            "waitress (Windows substitute for gunicorn; see README)",
        ),
        Target(
            "FastAPI",
            9002,
            [python, "-m", "uvicorn", "benchmarks.fastapi_app.app:application", "--host", "127.0.0.1", "--port", "9002"],
            "uvicorn (its standard/idiomatic server)",
        ),
        Target(
            "Hayavega (ASGI)",
            9004,
            [python, "-m", "uvicorn", "benchmarks.hayavega_asgi_app.app:application", "--host", "127.0.0.1", "--port", "9004"],
            "uvicorn (same server as the FastAPI target, for an apples-to-apples transport comparison)",
        ),
        Target(
            "Django",
            9003,
            # Fewer threads than Hayavega's waitress target, deliberately: each
            # Django request runs real middleware/URL-resolution Python under
            # the GIL (Hayavega's equivalent work is mostly in its Rust
            # runtime, which releases the GIL), so driving the same high
            # thread count here causes GIL-contention thrashing under load --
            # observed on a CPU-constrained CI runner as every request timing
            # out ("aborted due to deadline") rather than merely slower
            # responses. A small, CPU-sized pool avoids the thrash; the
            # matching --connection-limit still accepts the full load instead
            # of refusing connections, it just queues them for one of these
            # threads instead of spawning more context-switching workers.
            [python, "-m", "waitress", "--host=127.0.0.1", "--port=9003", "--threads=8", f"--connection-limit={connection_limit}", "benchmarks.django_app.app:application"],
            "waitress (Windows substitute for gunicorn; see README)",
        ),
    ]


def wait_until_ready(port: int, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/echo", timeout=1)
            return
        except Exception as error:  # noqa: BLE001 -- polling until the server accepts connections
            last_error = error
            time.sleep(0.3)
    raise RuntimeError(f"Server on port {port} did not become ready: {last_error}")


_SUMMARY_PATTERN = {
    "success_rate": re.compile(r"Success rate:\s*([\d.]+)%"),
    "requests_per_sec": re.compile(r"Requests/sec:\s*([\d.]+)"),
    "p50_ms": re.compile(r"50\.00% in ([\d.]+) ms"),
    "p90_ms": re.compile(r"90\.00% in ([\d.]+) ms"),
    "p99_ms": re.compile(r"99\.00% in ([\d.]+) ms"),
}


def parse_oha_output(text: str) -> ScenarioResult:
    values = {}
    for key, pattern in _SUMMARY_PATTERN.items():
        match = pattern.search(text)
        if not match:
            raise ValueError(f"Could not find {key!r} in oha output:\n{text}")
        values[key] = float(match.group(1))
    return ScenarioResult(
        requests_per_sec=values["requests_per_sec"],
        p50_ms=values["p50_ms"],
        p90_ms=values["p90_ms"],
        p99_ms=values["p99_ms"],
        success_rate=values["success_rate"],
    )


def run_oha(url: str, method: str, *, duration: str, connections: int) -> ScenarioResult:
    # -u ms pins the unit oha reports in "N.NN% in <value> <unit>" lines; without
    # it, oha auto-picks sec/ms/us per result, which silently breaks the parser
    # below whenever latencies happen to cross a unit boundary between runs.
    command = ["oha", "-z", duration, "-c", str(connections), "--no-tui", "-u", "ms", "-m", method, url]
    completed = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    return parse_oha_output(completed.stdout)


def benchmark_target(target: Target, *, duration: str, connections: int, warmup_requests: int, scenarios: list[tuple[str, str, str]] | None = None, runs: int = 1) -> TargetResult:
    scenarios = SCENARIOS if scenarios is None else scenarios
    result = TargetResult(target)
    log_slug = re.sub(r"[^a-z0-9]+", "_", target.name.lower()).strip("_")
    log_path = RESULTS_DIR / f"{log_slug}_server.log"
    RESULTS_DIR.mkdir(exist_ok=True)
    # The server's stdout/stderr MUST go to a file, not a PIPE we never read:
    # under real load, log lines (e.g. waitress's queue-depth warnings) fill
    # the OS pipe buffer, and a server blocked writing to a full pipe stops
    # answering requests entirely -- every request then times out, which
    # looks exactly like "the server is fine but too slow" if you don't know
    # to look for it. A file has no such backpressure.
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(target.command, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        try:
            wait_until_ready(target.port)
            base_url = f"http://127.0.0.1:{target.port}"
            # Warm up (import caches, JIT-ish effects, DB file creation) before measuring.
            run_oha(f"{base_url}{scenarios[0][2]}", "GET", duration="2s", connections=min(connections, 10))
            for name, method, path in scenarios:
                result.scenarios[name] = [
                    run_oha(f"{base_url}{path}", method, duration=duration, connections=connections)
                    for _ in range(runs)
                ]
        except Exception as error:  # noqa: BLE001 -- record and continue to the next target
            result.error = str(error)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return result


def render_markdown(results: list[TargetResult], *, duration: str, connections: int) -> str:
    lines = [
        "# Hayavega benchmark results",
        "",
        f"Generated by `benchmarks/run.py` (duration={duration}, concurrency={connections}).",
        "See `benchmarks/README.md` for methodology, scope, and honest caveats before drawing conclusions from these numbers.",
        "",
    ]
    for name, _method, _path in SCENARIOS:
        lines.append(f"## {name}")
        lines.append("")
        lines.append("| Framework | Server | req/s | p50 (ms) | p90 (ms) | p99 (ms) | Success |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for result in results:
            if result.error:
                lines.append(f"| {result.target.name} | {result.target.server} | -- | -- | -- | -- | error: {result.error} |")
                continue
            runs = result.scenarios[name]
            scenario = runs[0]
            if len(runs) > 1:
                agg = aggregate(runs)
                req_s = f"{agg.requests_per_sec_median:.0f} (min {agg.requests_per_sec_min:.0f}, max {agg.requests_per_sec_max:.0f})"
                p50 = f"{agg.p50_ms_median:.2f}"
                success = f"{agg.success_rate_min:.1f}%"
            else:
                req_s = f"{scenario.requests_per_sec:.0f}"
                p50 = f"{scenario.p50_ms:.2f}"
                success = f"{scenario.success_rate:.1f}%"
            lines.append(f"| {result.target.name} | {result.target.server} | {req_s} | {p50} | {scenario.p90_ms:.2f} | {scenario.p99_ms:.2f} | {success} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="10s", help="oha -z duration per scenario (default: 10s)")
    parser.add_argument("--connections", type=int, default=50, help="oha -c concurrency (default: 50)")
    parser.add_argument("--requests-warmup", type=int, default=50, dest="warmup_requests")
    parser.add_argument("--runs", type=int, default=1, help="Repeat each scenario this many times and report median + spread (default: 1)")
    args = parser.parse_args()

    if shutil.which("oha") is None:
        raise SystemExit("oha is not on PATH. See benchmarks/README.md for install instructions.")

    RESULTS_DIR.mkdir(exist_ok=True)
    results = [benchmark_target(target, duration=args.duration, connections=args.connections, warmup_requests=args.warmup_requests, runs=args.runs) for target in build_targets(args.connections)]

    for result in results:
        status = f"error: {result.error}" if result.error else "ok"
        print(f"{result.target.name}: {status}")

    markdown = render_markdown(results, duration=args.duration, connections=args.connections)
    (RESULTS_DIR / "RESULTS.md").write_text(markdown, encoding="utf-8")
    print(f"\nWrote {RESULTS_DIR / 'RESULTS.md'}")


if __name__ == "__main__":
    main()
