"""Benchmark the three native Rust functions that exist in mangoo_native but
are called from nowhere in src/mangoo/*.py (serialize_json, compile_selection,
normalize_path), against their pure-Python equivalents, before wiring any of
them in -- per this project's own stated principle: "Benchmark each candidate
first; schedule only once a measured win justifies it."

Requires mangoo_native to be built (see hatch_build.py / README.md).

Usage:
    python benchmarks/native_functions.py
"""

from __future__ import annotations

import timeit

import msgspec

try:
    from mangoo_native import compile_selection, normalize_path, serialize_json
except ImportError as error:
    raise SystemExit(f"mangoo_native is not built: {error}. See README.md's 'Building the Rust-backed dispatcher' section.") from error


# --- serialize_json vs msgspec.json.encode (Mangoo's current default) ------

_JSON_PAYLOAD = {"id": 42, "title": "Post 42", "views": 126, "tags": ["a", "b", "c"], "active": True, "score": 3.14159}


def _python_json() -> bytes:
    return msgspec.json.encode(_JSON_PAYLOAD)


def _native_json() -> bytes:
    return serialize_json(_JSON_PAYLOAD).encode("utf-8")


# --- compile_selection vs models.py's Selection._sql() string-building -----

_CLAUSES = [("views", ">", 10), ("title", "LIKE", "%post%")]
_ORDERING = [("id", "DESC")]


def _python_sql() -> str:
    query = "SELECT * FROM posts"
    query += " WHERE " + " AND ".join(f"{name} {operator} ?" for name, operator, _ in _CLAUSES)
    query += " ORDER BY " + ", ".join(f"{name} {direction}" for name, direction in _ORDERING)
    query += " LIMIT ? OFFSET ?"
    return query


def _native_sql() -> str:
    return compile_selection(
        "posts",
        [],  # empty fields list means "SELECT *" in mangoo-core's QueryPlan
        [(name, operator) for name, operator, _ in _CLAUSES],
        _ORDERING,
        20,
        0,
    )


# --- normalize_path vs a Python transliteration of the same operation ------
# (there is no live call site using this exact shape today -- runtime.py's
# own path handling does a plain .strip("/") for a different purpose, so
# this compares the operation itself, not a real swap-in.)


def _python_normalize(path: str) -> str:
    trimmed = path.strip()
    if not trimmed or trimmed == "/":
        return "/"
    return "/" + trimmed.strip("/")


def _native_normalize(path: str) -> str:
    return normalize_path(path)


_PATH_SAMPLES = ["/api/v1/items/42/", "items", "", "/", "//double//slash//"]


def _run_paths(fn) -> None:
    for sample in _PATH_SAMPLES:
        fn(sample)


def _report(name: str, python_fn, native_fn, *, number: int = 200_000) -> None:
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
    assert _python_json() == _native_json(), "python/native JSON output must match"
    assert _python_sql() == _native_sql(), f"python/native SQL must match:\n{_python_sql()}\n{_native_sql()}"
    for sample in _PATH_SAMPLES:
        assert _python_normalize(sample) == _native_normalize(sample), f"mismatch for {sample!r}: {_python_normalize(sample)!r} != {_native_normalize(sample)!r}"

    _report("serialize_json (msgspec) vs native serialize_json", _python_json, _native_json)
    _report("compile_selection: Python string-building vs native", _python_sql, _native_sql)
    _report("normalize_path: Python vs native", lambda: _run_paths(_python_normalize), lambda: _run_paths(_native_normalize), number=40_000)


if __name__ == "__main__":
    main()
