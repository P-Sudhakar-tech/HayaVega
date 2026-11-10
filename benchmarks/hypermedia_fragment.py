"""Measure what Hayavega's hypermedia (htmx-style) update pattern actually
saves for a real interaction, versus a full page reload.

This is intentionally Hayavega-only, not a cross-framework comparison:
FastAPI and Django don't ship a hypermedia runtime out of the box (adding
one would mean benchmarking a third-party library's overhead, the same
reasoning benchmarks/README.md gives for excluding GraphQL from the
cross-framework table). `hayavega.ui`'s `hayavega-ui.js` already implements
the real htmx attribute convention (hx-get/hx-target/hx-swap) for
partial-fragment swaps; what this measures is the server-side cost of
serving what an hx-get would fetch (GET /posts/fragment, just the <ul>)
against a full page load (GET /posts) for the same 20 rows of data.

Requires oha on PATH; reuses benchmarks/run.py's server-lifecycle and
oha-parsing helpers rather than duplicating them.

Usage:
    python benchmarks/hypermedia_fragment.py [--duration 10s] [--connections 30]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run import RESULTS_DIR, Target, benchmark_target  # noqa: E402 -- see sys.path.insert above

FRAGMENT_SCENARIOS = [
    ("full_page", "GET", "/posts"),
    ("hx_fragment", "GET", "/posts/fragment"),
    ("hx_fragment_native", "GET", "/posts/fragment-native"),
]


def build_target() -> Target:
    return Target(
        "Hayavega",
        9005,
        [sys.executable, "-m", "waitress", "--host=127.0.0.1", "--port=9005", "--threads=16", "--call", "benchmarks.hayavega_app.app:build_application"],
        "waitress",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--connections", type=int, default=30)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    result = benchmark_target(build_target(), duration=args.duration, connections=args.connections, warmup_requests=50, scenarios=FRAGMENT_SCENARIOS)
    if result.error:
        raise SystemExit(f"Benchmark failed: {result.error}")

    full_page = result.scenarios["full_page"]
    fragment = result.scenarios["hx_fragment"]
    native_fragment = result.scenarios["hx_fragment_native"]

    print("Full page (GET /posts) vs Python fragment (GET /posts/fragment) vs Rust-rendered fragment (GET /posts/fragment-native), same 20 rows:")
    print(f"  full_page:          {full_page.requests_per_sec:.0f} req/s, p50={full_page.p50_ms:.2f}ms p99={full_page.p99_ms:.2f}ms")
    print(f"  hx_fragment:        {fragment.requests_per_sec:.0f} req/s, p50={fragment.p50_ms:.2f}ms p99={fragment.p99_ms:.2f}ms")
    print(f"  hx_fragment_native: {native_fragment.requests_per_sec:.0f} req/s, p50={native_fragment.p50_ms:.2f}ms p99={native_fragment.p99_ms:.2f}ms")
    print(f"  -> python fragment is {fragment.requests_per_sec / full_page.requests_per_sec:.2f}x the full page's throughput")
    print(f"  -> native fragment is {native_fragment.requests_per_sec / fragment.requests_per_sec:.2f}x the python fragment's throughput")


if __name__ == "__main__":
    main()
