"""HTTP-level benchmark: does moving route matching to Rust
(hayavega_native.Router, see crates/hayavega-python/src/lib.rs) help, when
measured as it would actually be used -- once per real HTTP request,
not in a tight microbenchmark loop?

Phase 1 of RUST_CORE_REWRITE_ROADMAP.md. A microbenchmark here would just
re-measure what normalize_path's already-rejected candidate measured (a
tiny Python operation vs. the same operation behind a PyO3 FFI call) --
see that candidate's result in benchmarks/README.md before assuming this
one wins just because it's wrapped differently. This compares two real
WSGI applications, same app (benchmarks/hayavega_app's exact routes/views/
seed data), same server (waitress, both sides, so only the matching logic
differs -- unlike rust_runtime_vs_wsgi.py, which also changes the server):

    - Baseline: benchmarks/hayavega_app/app.py's real, unmodified
      create_application() (runtime.py's Python dict-based _match()).
    - Candidate: benchmarks/_native_router_target.py's build_application()
      (hayavega_native.Router for matching; everything else identical).

Run: python benchmarks/native_router_vs_python.py [--duration 10s] [--connections 30]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks._vertical_slice_harness import benchmark_target, print_scenario_results, print_summary, require_oha  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--connections", type=int, default=30)
    args = parser.parse_args()

    require_oha()

    python = sys.executable
    targets = [
        ("Python _match()", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9007", "--threads=16", "--call", "benchmarks.hayavega_app.app:build_application"], 9007),
        ("Native Router", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9008", "--threads=16", "--call", "benchmarks._native_router_target:build_application"], 9008),
    ]

    print(f"Benchmark: hayavega_native.Router vs runtime.py's Python _match() (duration={args.duration}, concurrency={args.connections})\n")
    all_results: dict[str, dict] = {}
    for name, command, port in targets:
        print(f"--- {name} ---")
        try:
            all_results[name] = benchmark_target(command, port, duration=args.duration, connections=args.connections)
            print_scenario_results(all_results[name])
        except Exception as error:  # noqa: BLE001 -- report the failure and keep going with the other target
            print(f"  FAILED: {error}")
        print()

    print_summary("Native Router vs Python _match()", all_results, baseline_name="Python _match()", candidate_name="Native Router")


if __name__ == "__main__":
    main()
