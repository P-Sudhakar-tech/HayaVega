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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks._vertical_slice_harness import ROOT, benchmark_target, print_scenario_results, print_summary, require_oha  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--connections", type=int, default=30)
    args = parser.parse_args()

    require_oha()

    python = sys.executable
    targets = [
        ("Rust runtime", [python, str(ROOT / "benchmarks" / "_rust_runtime_target.py")], 9006),
        ("Python runtime (waitress)", [python, "-m", "waitress", "--host=127.0.0.1", "--port=9007", "--threads=16", "--call", "benchmarks.mangoo_app.app:build_application"], 9007),
    ]

    print(f"Benchmark: mangoo.rust_runtime vs waitress (duration={args.duration}, concurrency={args.connections})\n")
    all_results: dict[str, dict] = {}
    for name, command, port in targets:
        print(f"--- {name} ---")
        try:
            all_results[name] = benchmark_target(command, port, duration=args.duration, connections=args.connections)
            print_scenario_results(all_results[name])
        except Exception as error:  # noqa: BLE001 -- report the failure and keep going with the other target
            print(f"  FAILED: {error}")
        print()

    print_summary("Rust runtime vs waitress", all_results, baseline_name="Python runtime (waitress)", candidate_name="Rust runtime")


if __name__ == "__main__":
    main()
