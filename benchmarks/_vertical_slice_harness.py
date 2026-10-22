"""Shared HTTP-level benchmark harness for vertical-slice Rust-acceleration
candidates (see RUST_CORE_REWRITE_ROADMAP.md) -- extracted from
rust_runtime_vs_wsgi.py's own inline copy of this logic so every later
phase's benchmark script reuses it instead of re-copy-pasting the oha-
wrangling. Not a benchmark to run directly -- see
benchmarks/rust_runtime_vs_wsgi.py and benchmarks/native_router_vs_python.py
for actual usage.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SCENARIOS = [
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


def require_oha() -> None:
    if shutil.which("oha") is None:
        raise SystemExit("oha is not on PATH. See benchmarks/README.md for install instructions.")


def wait_until_ready(port: int, *, path: str = "/echo", timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1)
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


def benchmark_target(command: list[str], port: int, *, duration: str, connections: int, scenarios: list[tuple[str, str]] = DEFAULT_SCENARIOS, ready_path: str = "/echo") -> dict[str, ScenarioResult]:
    """Run one target (a subprocess serving `command`) through `scenarios`,
    returning one `ScenarioResult` per scenario name."""
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_until_ready(port, path=ready_path)
        run_oha(f"http://127.0.0.1:{port}{ready_path}", duration="2s", connections=min(connections, 10))  # warm-up
        results = {}
        for scenario_name, scenario_path in scenarios:
            results[scenario_name] = run_oha(f"http://127.0.0.1:{port}{scenario_path}", duration=duration, connections=connections)
        return results
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def print_scenario_results(results: dict[str, ScenarioResult]) -> None:
    for scenario_name, result in results.items():
        print(f"  {scenario_name:18s} {result.requests_per_sec:8.1f} req/s  p50={result.p50_ms:7.2f}ms  p90={result.p90_ms:7.2f}ms  p99={result.p99_ms:7.2f}ms  success={result.success_rate:.1f}%")


def print_summary(label: str, all_results: dict[str, dict[str, ScenarioResult]], *, baseline_name: str, candidate_name: str, scenarios: list[tuple[str, str]] = DEFAULT_SCENARIOS) -> None:
    """Print a ratio summary (candidate req/s / baseline req/s) for every
    scenario both targets completed."""
    if len(all_results) != 2:
        return
    print(f"--- Summary ({label}) ---")
    candidate = all_results.get(candidate_name, {})
    baseline = all_results.get(baseline_name, {})
    for scenario_name, _ in scenarios:
        if scenario_name in candidate and scenario_name in baseline:
            ratio = candidate[scenario_name].requests_per_sec / baseline[scenario_name].requests_per_sec
            print(f"  {scenario_name:18s} {candidate_name}={candidate[scenario_name].requests_per_sec:8.1f} req/s  {baseline_name}={baseline[scenario_name].requests_per_sec:8.1f} req/s  ratio={ratio:.2f}x")
