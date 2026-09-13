"""Benchmark two more native-Rust candidates against their current Python
paths, following up on the finding in native_functions.py that Python beat
all three of the framework's existing inert Rust functions.

These two candidates were chosen because they target the specific weak
spots the FastAPI/Django benchmark suite (benchmarks/README.md) surfaced:

1. Template rendering: Mangoo loses `template_render` to FastAPI's Jinja2
   on both transports. `render_post_list_page` is a hand-compiled,
   best-case Rust stand-in for exactly one template
   (benchmarks/mangoo_app/templates/page.html) -- not a general engine.
   If even this best case doesn't beat Python by a solid margin, building
   a *general* Rust template compiler (a much bigger project) isn't
   justified either.

2. ASGI per-request overhead: mangoo.asgi's request/response header
   handling runs on every request regardless of view cost, unlike the
   view itself. `decode_request_headers`/`encode_response_headers` mirror
   that exact logic.

Requires mangoo_native to be built (see hatch_build.py / README.md).

Usage:
    python benchmarks/native_template_and_asgi.py
"""

from __future__ import annotations

import sys
import timeit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from mangoo_native import decode_request_headers as _native_decode_headers
    from mangoo_native import encode_response_headers as _native_encode_headers
    from mangoo_native import render_post_list_page as _native_render_page
except ImportError as error:
    raise SystemExit(f"mangoo_native is not built: {error}. See README.md's 'Building the Rust-backed dispatcher' section.") from error

from mangoo.templates import render_template

# --- Template rendering: Python's general interpreter vs a Rust best case --

_TEMPLATE_DIR = Path(__file__).resolve().parent / "mangoo_app" / "templates"
_POSTS = [{"title": f"Post {i}", "views": i * 3} for i in range(20)]
_ROWS = [(post["title"], post["views"]) for post in _POSTS]


def _python_render() -> str:
    return render_template("page.html", {"posts": _POSTS}, directories=(_TEMPLATE_DIR,))


def _native_render() -> str:
    return _native_render_page(_ROWS)


# --- ASGI request header decoding: dict comprehension vs native -----------

_RAW_REQUEST_HEADERS = [
    (b"host", b"example.com"),
    (b"user-agent", b"Mozilla/5.0 (compatible; benchmark)"),
    (b"accept", b"application/json"),
    (b"accept-encoding", b"gzip, deflate, br"),
    (b"content-type", b"application/json"),
    (b"content-length", b"128"),
    (b"cookie", b"mangoo_session=abcdef0123456789"),
    (b"x-request-id", b"11111111222233334444555566667777"),
]


def _python_decode_headers() -> dict[str, str]:
    return {raw_name.decode("latin-1").title(): raw_value.decode("latin-1") for raw_name, raw_value in _RAW_REQUEST_HEADERS}


def _native_decode() -> dict[str, str]:
    return dict(_native_decode_headers(_RAW_REQUEST_HEADERS))


# --- ASGI response header encoding: list comprehension vs native ----------

_RESPONSE_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Content-Security-Policy", "default-src 'self'"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("X-Request-ID", "11111111222233334444555566667777"),
]


def _python_encode_headers() -> list[tuple[bytes, bytes]]:
    return [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in _RESPONSE_HEADERS]


def _native_encode() -> list[tuple[bytes, bytes]]:
    return _native_encode_headers(_RESPONSE_HEADERS)


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
    assert _python_render() == _native_render(), "python/native template output must match byte-for-byte"
    assert _python_decode_headers() == _native_decode(), "python/native header decoding must match"
    assert _python_encode_headers() == _native_encode(), "python/native header encoding must match"

    _report("template render: page.html (Python interpreter) vs native best-case", _python_render, _native_render, number=20_000)
    _report("ASGI request header decode: Python dict comp vs native", _python_decode_headers, _native_decode, number=100_000)
    _report("ASGI response header encode: Python list comp vs native", _python_encode_headers, _native_encode, number=100_000)


if __name__ == "__main__":
    main()
