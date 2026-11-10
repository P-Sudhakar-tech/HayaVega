"""HTTP-level benchmark: does hayavega_native.SerializedConnection (one
Rust-owned sqlite3 connection, every query serialized through a single
worker thread) beat Python's existing options for db_list's actual code
path (Selection.values() -> connect_readonly())?

Phase 3 (narrowed) of RUST_CORE_REWRITE_ROADMAP.md. A three-way
comparison, not two, since the point is "better than Python's *best*
existing option," not just "better than doing nothing":

    1. No pooling (today's default) -- a fresh connect_readonly() per request.
    2. db.enable_readonly_connection_pooling() -- Python's existing best
       option for this exact question; already measured at HTTP level
       against this exact db_list scenario (see db.py's own docstring)
       and found to be within normal run-to-run variance, neither a real
       win nor a real loss.
    3. hayavega_native.SerializedConnection -- one Rust-owned connection,
       serializing every query through one worker thread instead of each
       request thread holding (or sharing) its own.

Scoped to the one scenario this actually tests (/posts.json, the route
Selection.values() serves) -- see this file's targets' own docstrings
for why the DB-access path is isolated while everything else
(routing, Response/JSON encoding) stays the real framework code.

Run: python benchmarks/serialized_connection_vs_python.py [--duration 10s] [--connections 30]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks._vertical_slice_harness import benchmark_target, print_scenario_results, print_summary, require_oha  # noqa: E402

_SCENARIOS = [("db_list", "/posts.json")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--connections", type=int, default=30)
    args = parser.parse_args()

    require_oha()

    python = sys.executable
    targets = [
        ("No pooling (default)", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9007", "--threads=16", "--call", "benchmarks.hayavega_app.app:build_application"], 9007),
        ("Readonly pooling", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9012", "--threads=16", "--call", "benchmarks._readonly_pooling_target:build_application"], 9012),
        ("Serialized Connection", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9011", "--threads=16", "--call", "benchmarks._serialized_connection_target:build_application"], 9011),
    ]

    print(f"Benchmark: hayavega_native.SerializedConnection vs Python's DB-access options (duration={args.duration}, concurrency={args.connections})\n")
    all_results: dict[str, dict] = {}
    for name, command, port in targets:
        print(f"--- {name} ---")
        try:
            all_results[name] = benchmark_target(command, port, duration=args.duration, connections=args.connections, scenarios=_SCENARIOS, ready_path="/posts.json")
            print_scenario_results(all_results[name])
        except Exception as error:  # noqa: BLE001 -- report the failure and keep going with the other targets
            print(f"  FAILED: {error}")
        print()

    print_summary("Serialized Connection vs No pooling", all_results, baseline_name="No pooling (default)", candidate_name="Serialized Connection", scenarios=_SCENARIOS)
    print_summary("Serialized Connection vs Readonly pooling", all_results, baseline_name="Readonly pooling", candidate_name="Serialized Connection", scenarios=_SCENARIOS)
    print_summary("Readonly pooling vs No pooling (sanity check against db.py's own documented result)", all_results, baseline_name="No pooling (default)", candidate_name="Readonly pooling", scenarios=_SCENARIOS)


if __name__ == "__main__":
    main()
