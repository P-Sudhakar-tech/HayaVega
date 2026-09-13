"""Memory profiling for templates.py's render path, using whichever tool
actually fits the question being asked -- this file documents what was
tried and why, since two of the three obvious choices turned out to have
real, non-obvious limitations for this specific workload (see
benchmarks/README.md's "Line-by-line memory profiling" section for the
full writeup).

- Scalene (the initially preferred choice for combined CPU+memory,
  per-line, native-code-aware profiling) FAILS TO BUILD on this repo's
  Windows dev environment: it needs Microsoft C++ Build Tools, which
  aren't installed. This is a genuine Scalene-on-Windows gap, not a
  mistake in this setup -- documented, same treatment this repo already
  gives gunicorn's Windows limitation. Usable via WSL/Linux/macOS if a
  contributor wants that deeper view (it can also attribute memory to
  mangoo_native's Rust code, which pure-Python tools cannot).

- memory_profiler's @profile samples process RSS per source line. For a
  tight loop like a template render, CPython's own reference counting
  frees each iteration's garbage immediately, so RSS barely moves between
  samples -- @profile correctly shows ~0.0 MiB per line here, which is
  the right answer to "is anything leaking or growing unboundedly" (it
  is not) but the wrong tool for "which line allocates the most churn."

- tracemalloc (stdlib, already used in template_context_fix.py) measures
  actual Python allocator activity, not RSS -- but a plain before/after
  snapshot diff has the same blind spot for a different reason: by the
  time the "after" snapshot is taken, most of a single render's internal
  garbage has already been freed by refcounting, so the diff mostly shows
  only what survived (the returned string, cache entries). What DOES work,
  and is what template_context_fix.py already relies on: sampling
  `tracemalloc.get_traced_memory()`'s PEAK value across a batch of calls
  while tracing stays active -- that catches each call's live-at-peak
  footprint before it's freed, not just what's left over afterward.

Net result: this file exists to (1) prove there's no leak over sustained
rendering (memory_profiler, real tool for that question) and (2) point at
template_context_fix.py for peak-memory-per-implementation comparisons
(tracemalloc, real tool for that question) -- rather than pretend a single
tool answers both.

Requires: pip install memory_profiler (optional dev dependency, not
shipped with the framework or added to pyproject.toml -- same
manual-install treatment benchmarks/README.md already gives
fastapi/uvicorn/django/jinja2/waitress, none of which mangoo depends on).

Usage:
    python -m memory_profiler benchmarks/memory_profile_templates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory_profiler import profile

from mangoo.templates import render_template

_TEMPLATE_DIR = Path(__file__).resolve().parent / "mangoo_app" / "templates"
_CONTEXT = {"posts": [{"title": f"Post {i}", "views": i * 3} for i in range(20)]}


@profile
def render_many(count: int) -> None:
    """Confirms rendering doesn't leak or grow RSS across sustained use --
    the question memory_profiler is actually suited to answer here."""
    for _ in range(count):
        render_template("page.html", _CONTEXT, directories=(_TEMPLATE_DIR,))


if __name__ == "__main__":
    render_many(20_000)
