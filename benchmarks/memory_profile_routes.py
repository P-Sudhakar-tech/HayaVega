"""Per-request memory profiling for Hayavega's own benchmark routes (the
same four scenarios benchmarks/run.py measures throughput for: json_echo,
db_list, template_render, static_file), using the proven tracemalloc-peak
methodology this project already established in
memory_profile_templates.py's own writeup -- see that file's docstring for
why RSS-based tools (memory_profiler) and Scalene (fails to build on this
repo's Windows dev environment without MS C++ Build Tools) don't fit this
question, and why tracemalloc.get_traced_memory()'s PEAK value across a
batch of calls, not a before/after snapshot diff, is the real answer.

Unlike benchmarks/run.py's HTTP-level benchmarking (which spawns each
target as a separate OS process and can't see inside it), this calls
hayavega.runtime.create_application's real WSGI callable directly, in the
same process as the measurement -- tracemalloc can only observe the
process it runs in, so an HTTP-level, subprocess-based benchmark
structurally cannot answer "how much does this one call allocate." This
does mean the other cross-framework targets (FastAPI, Django, Granian,
...) are out of scope for this script: it measures Hayavega's own routes
only, the same scope benchmarks/perf_gate.py's CI regression gate already
has for throughput.

No regression threshold is enforced yet -- this establishes the
measurement and prints it on every CI run (see the `perf` job in
.github/workflows/ci.yml) so a future regression is visible in the job's
own output, the same first step benchmarks/perf_gate.py's `db_list`
scenario took before a stable-enough baseline justified gating on it.
Turning this into a real blocking gate (a committed baseline + threshold)
is open follow-up work, not claimed as done here.

Usage:
    python benchmarks/memory_profile_routes.py [--calls 2000]
"""

from __future__ import annotations

import argparse
import sys
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.hayavega_app.app import seed, urlpatterns  # noqa: E402
from hayavega.runtime import create_application  # noqa: E402

ROUTES = {
    "json_echo": "/echo",
    "db_list": "/posts.json",
    "template_render": "/posts",
    "static_file": "/static/logo.svg",
}


def call(application, path: str) -> None:
    captured = {}

    def start_response(status, headers):
        captured["status"] = status

    environ = {
        "PATH_INFO": path,
        "REQUEST_METHOD": "GET",
        "HTTP_HOST": "localhost",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
        "wsgi.url_scheme": "http",
    }
    b"".join(application(environ, start_response))


def profile_route(application, path: str, calls: int) -> tuple[float, float, float]:
    """Returns (median, p90, max) peak bytes across `calls` repeated requests."""
    tracemalloc.start()
    peaks = []
    for _ in range(calls):
        tracemalloc.clear_traces()
        call(application, path)
        _, peak = tracemalloc.get_traced_memory()
        peaks.append(peak)
    tracemalloc.stop()
    peaks.sort()
    return peaks[len(peaks) // 2], peaks[int(len(peaks) * 0.9)], max(peaks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=2000, help="repeated calls per route (default: 2000)")
    args = parser.parse_args()

    seed()
    application = create_application(urlpatterns)

    # Warm up (import caches, template compile cache, resolved-path cache)
    # before measuring -- the same reason benchmarks/run.py's HTTP
    # benchmarks warm up first.
    for route_path in ROUTES.values():
        call(application, route_path)

    print(f"Per-request peak memory, {args.calls} calls per route (tracemalloc):\n")
    for name, route_path in ROUTES.items():
        median, p90, peak_max = profile_route(application, route_path, args.calls)
        print(f"{name:20s} median={median / 1024:8.2f} KiB  p90={p90 / 1024:8.2f} KiB  max={peak_max / 1024:8.2f} KiB")


if __name__ == "__main__":
    main()
