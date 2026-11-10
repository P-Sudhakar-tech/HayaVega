"""Benchmarks hayavega_native's `plain_value` -- a Rust replacement for
serializers.py's `_plain()`, the recursive object-graph walk every API
response passes through before `msgspec.json.encode()` -- against
realistic payload shapes. Per this project's own stated principle:
"Benchmark each candidate first; schedule only once a measured win
justifies it."

**Result: rejected, same as `native_functions.py`'s three candidates.**
Python wins 1.33-1.59x across a flat dict, a single Entity, and a
30-row Entity list (matching `benchmarks/run.py`'s own `db_list` row
count, not a trivial single-field case). Unlike those three, `_plain()`
wasn't racing an already-optimized C library -- there's no C equivalent
of "walk an arbitrary Entity/dict/list graph" to lose to -- but it hit
the same structural wall `native_htmx_fragment.py`'s rejected general
template engine did (1.4-2.1x loss there): a recursive walk calling
back into Python at every node pays one FFI crossing per node, and that
cost dominates for a graph this shallow. `hayavega_native.plain_value`
stays in the codebase, unwired, as a documented, benchmarked rejection
-- see `UPCOMING_IMPROVEMENT_ROADMAP.md`.

Requires hayavega_native to be built (see hatch_build.py / README.md).

Usage:
    python benchmarks/native_plain_value.py
"""

from __future__ import annotations

import timeit
from datetime import datetime, timezone

try:
    from hayavega_native import plain_value as _native_plain
except ImportError as error:
    raise SystemExit(f"hayavega_native is not built: {error}. See README.md's 'Building the Rust-backed dispatcher' section.") from error

from hayavega.models import Entity, Integer, Key, Text, Timestamp
from hayavega.serializers import _plain as _python_plain

# --- realistic payload shapes ------------------------------------------


class Post(Entity):
    __table__ = "posts"
    id = Key()
    title = Text()
    views = Integer()
    published_at = Timestamp()


_FLAT_DICT = {"id": 42, "title": "Post 42", "views": 126, "tags": ["a", "b", "c"], "active": True, "score": 3.14159}

_PUBLISHED_AT = datetime(2024, 1, 15, 12, 30, tzinfo=timezone.utc)
_NESTED_POST = Post(id=1, title="A nested post", views=100, published_at=_PUBLISHED_AT)

_POST_LIST = [Post(id=index, title=f"Post {index}", views=index * 3, published_at=_PUBLISHED_AT) for index in range(30)]


def _python_flat() -> dict:
    return _python_plain(_FLAT_DICT)


def _native_flat() -> dict:
    return _native_plain(_FLAT_DICT)


def _python_entity() -> dict:
    return _python_plain(_NESTED_POST)


def _native_entity() -> dict:
    return _native_plain(_NESTED_POST)


def _python_list() -> list:
    return _python_plain(_POST_LIST)


def _native_list() -> list:
    return _native_plain(_POST_LIST)


def _report(name: str, python_fn, native_fn, *, number: int = 50_000) -> None:
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
    assert _python_flat() == _native_flat(), "python/native output must match for a flat dict"
    assert _python_entity() == _native_entity(), "python/native output must match for a nested Entity"
    assert _python_list() == _native_list(), "python/native output must match for a 30-row Entity list"

    _report("_plain(): flat dict (6 fields, 1 level)", _python_flat, _native_flat)
    _report("_plain(): single Entity (as_dict + a Timestamp field)", _python_entity, _native_entity)
    _report("_plain(): 30-row Entity list (matches db_list's own row count)", _python_list, _native_list, number=10_000)


if __name__ == "__main__":
    main()
