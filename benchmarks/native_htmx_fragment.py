"""Benchmark a hardcoded Rust renderer against Mangoo's Python `mangoo.ui`
component builders, for the specific shape of work an htmx fragment swap
does: rendering a small, fixed-shape HTML snippet, not a full page.

This follows up on two earlier findings this session: (1) a hardcoded Rust
function for a *full page* won by 16-20x but wasn't shippable as a general
solution, and (2) a *general* Rust template engine lost to Python by
1.4-2.1x once it had to support arbitrary template syntax. Fragments are a
different shape of problem from both: they're naturally narrow (a fragment
renders one specific, small piece of markup -- a list, a row, an item),
so a hardcoded per-fragment renderer's lack of generality is much less of
a real limitation than it was for a whole page. This tests two sizes:
the benchmark app's 20-item list fragment, and a single-item fragment (the
more realistic unit of a real htmx "add one item" interaction).

Requires mangoo_native to be built (see hatch_build.py / README.md).

Usage:
    python benchmarks/native_htmx_fragment.py
"""

from __future__ import annotations

import sys
import timeit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from mangoo_native import render_fragment_items
except ImportError as error:
    raise SystemExit(f"mangoo_native is not built: {error}. See README.md's 'Building the Rust-backed dispatcher' section.") from error

from mangoo.ui import li, ul

_TWENTY_ITEMS = [(f"Post {i}", i * 3) for i in range(20)]
_ONE_ITEM = [("New item", 0)]


def _python_fragment(items: list[tuple[str, int]]) -> str:
    return str(ul(*(li(f"{title} ({views} views)") for title, views in items)))


def _native_fragment(items: list[tuple[str, int]]) -> str:
    return render_fragment_items(items)


def _report(name: str, python_fn, native_fn, *, number: int) -> None:
    python_time = timeit.timeit(python_fn, number=number)
    native_time = timeit.timeit(native_fn, number=number)
    faster = "native" if native_time < python_time else "python"
    ratio = python_time / native_time if native_time else float("inf")
    print(f"{name}:")
    print(f"  python: {python_time:.4f}s for {number:,} calls ({python_time / number * 1e6:.3f} us/call)")
    print(f"  native: {native_time:.4f}s for {number:,} calls ({native_time / number * 1e6:.3f} us/call)")
    print(f"  -> {faster} is faster ({ratio:.2f}x)" if faster == "native" else f"  -> {faster} is faster ({1 / ratio:.2f}x)")
    print()


def main() -> None:
    assert _python_fragment(_TWENTY_ITEMS) == _native_fragment(_TWENTY_ITEMS), "python/native 20-item fragment output must match byte-for-byte"
    assert _python_fragment(_ONE_ITEM) == _native_fragment(_ONE_ITEM), "python/native 1-item fragment output must match byte-for-byte"

    _report("htmx fragment: 20-item list (the benchmark app's /posts/fragment)", lambda: _python_fragment(_TWENTY_ITEMS), lambda: _native_fragment(_TWENTY_ITEMS), number=50_000)
    _report("htmx fragment: 1-item (a realistic single 'add item' interaction)", lambda: _python_fragment(_ONE_ITEM), lambda: _native_fragment(_ONE_ITEM), number=50_000)


if __name__ == "__main__":
    main()
