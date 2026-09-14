"""Opt-in Rust-backed WSGI server, selected via `SERVER['ENGINE'] = 'rust'`
in a project's settings module (default remains `'python'`, i.e. today's
`wsgiref`-based `mangoo.runtime.serve`).

Scope decision, disclosed here and in `mangoo_native`'s `server.rs`: this is
a synchronous, fixed-thread-pool Rust HTTP/1.1 server (`mangoo_native.serve_wsgi`)
-- the same concurrency model `waitress` already uses -- not a `tokio`/
`hyper`-based async runtime. CPython's GIL means only one thread ever
executes Python view code at a time regardless of how the socket-accept
loop is built, so async I/O would not let more Python code run
concurrently than this design already does; Rust's role here is the same
narrow one every other native component in this codebase plays (own
socket accept, HTTP/1.1 parsing, and response writing around unchanged
Python request-handling code via the standard WSGI contract).

No HTTP keep-alive yet: every connection is closed after one response.
This is a real, disclosed limitation of this first version, not an
oversight -- see `crates/mangoo-python/src/server.rs`'s module docstring.
"""

from __future__ import annotations

from .hardening import HardeningOptions
from .runtime import create_application


class RustRuntimeUnavailableError(RuntimeError):
    pass


def serve(urlpatterns, host: str = "127.0.0.1", port: int = 8000, *, hardening: HardeningOptions | None = None, debug: bool = False, workers: int = 8) -> None:
    """Build the same WSGI application `mangoo.runtime.serve` would and run
    it behind the native Rust server instead of `wsgiref`.

    Raises `RustRuntimeUnavailableError` with an actionable message if
    `mangoo_native` was not built for this install (e.g. `cargo` was not on
    `PATH` during `pip install`, or `MANGOO_SKIP_NATIVE_BUILD=1` was set) --
    the same situation every other `mangoo_native`-dependent feature in this
    codebase (Signals' Rust dispatcher, `mango_list`) already handles by
    falling back to a pure-Python equivalent; a WSGI *server* has no
    equivalent fallback to fall back to within this same function, so the
    caller (`SERVER['ENGINE'] = 'rust'` in settings, checked by
    `mango launch`) is told plainly instead of silently starting the
    default Python server under a different name.
    """
    try:
        import mangoo_native
    except ImportError as error:
        raise RustRuntimeUnavailableError("SERVER['ENGINE'] is 'rust' but mangoo_native was not built for this install. Rebuild with `cargo` on PATH (`pip install -e .` re-runs the build hook), or set SERVER['ENGINE'] to 'python' to use the standard runtime.") from error

    application = create_application(urlpatterns, hardening=hardening, debug=debug)
    print(f"Mangoo development server (Rust runtime, {workers} workers): http://{host}:{port}/")
    mangoo_native.serve_wsgi(application, host, port, workers)
