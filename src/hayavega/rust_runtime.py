"""Opt-in Rust-backed WSGI server, selected via `Settings.server.engine = 'rust'`
in a project's settings module (default remains `'python'`, i.e. today's
`wsgiref`-based `hayavega.runtime.serve`).

Scope decision, disclosed here and in `hayavega_native`'s `server.rs`: this is
a synchronous, fixed-thread-pool Rust HTTP/1.1 server (`hayavega_native.serve_wsgi`)
-- the same concurrency model `waitress` already uses -- not a `tokio`/
`hyper`-based async runtime. CPython's GIL means only one thread ever
executes Python view code at a time regardless of how the socket-accept
loop is built, so async I/O would not let more Python code run
concurrently than this design already does; Rust's role here is the same
narrow one every other native component in this codebase plays (own
socket accept, HTTP/1.1 parsing, and response writing around unchanged
Python request-handling code via the standard WSGI contract).

Real HTTP/1.1 keep-alive is implemented, and the server is hardened
against the well-known failure modes of a hand-written HTTP server
(request smuggling, response splitting, Slowloris, a panicking connection
permanently shrinking the worker pool) -- see
`crates/hayavega-python/src/server.rs`'s module docstring for the full list
and `benchmarks/README.md` for the benchmarked, disclosed result.

Optional TLS termination is supported (`Settings.server.tls_cert`/
`Settings.server.tls_key`, PEM file paths, via `rustls`) for local HTTPS
development and testing. HTTP/2 remains explicitly out of scope: it is
built around multiplexing many streams over one connection, which does not
fit this server's synchronous thread-per-connection model without
effectively becoming its own scheduler, and its main real-world benefit
(parallel loading of many small browser-requested resources) does not
apply to a backend API server the way it does to a web page -- a reverse
proxy remains the right place for HTTP/2 and for production TLS
termination, matching this project's own `DEPLOYMENT.md` guidance for
`gunicorn`/`waitress`.
"""

from __future__ import annotations

import os
import sys

from .hardening import HardeningOptions
from .runtime import create_application


class RustRuntimeUnavailableError(RuntimeError):
    pass


def serve(urlpatterns, host: str = "127.0.0.1", port: int = 8000, *, hardening: HardeningOptions | None = None, debug: bool = False, workers: int = 8, processes: int = 1, tls_cert: str | None = None, tls_key: str | None = None) -> None:
    """Build the same WSGI application `hayavega.runtime.serve` would and run
    it behind the native Rust server instead of `wsgiref`.

    `tls_cert`/`tls_key` (PEM file paths) serve the application over HTTPS
    instead of plain HTTP when both are given; both must be provided
    together (`hayavega_native.serve_wsgi` rejects one without the other with
    a clear error rather than silently serving plaintext). The framework's
    own `Request.is_secure`, and therefore `Hardening`'s `FORCE_HTTPS`/
    secure-cookie logic, work correctly under TLS exactly as they do behind
    a TLS-terminating reverse proxy -- `wsgi.url_scheme` is set to
    `"https"` for every request served this way.

    Raises `RustRuntimeUnavailableError` with an actionable message if
    `hayavega_native` was not built for this install (e.g. `cargo` was not on
    `PATH` during `pip install`, or `HAYAVEGA_SKIP_NATIVE_BUILD=1` was set) --
    the same situation every other `hayavega_native`-dependent feature in this
    codebase (Signals' Rust dispatcher, `mango_list`) already handles by
    falling back to a pure-Python equivalent; a WSGI *server* has no
    equivalent fallback to fall back to within this same function, so the
    caller (`Settings.server.engine = 'rust'` in settings, checked by
    `hayavega launch`) is told plainly instead of silently starting the
    default Python server under a different name.

    `processes` (default 1, i.e. today's single-process behavior) runs
    `processes` separate OS processes, each with its own Python interpreter
    and GIL, all binding the same `host:port` via `SO_REUSEPORT` so the
    kernel load-balances connections across them -- this is the real way to
    use more than one CPU core: within a single process, CPython's GIL caps
    Python view-code execution to one thread at a time no matter how large
    `workers` is (measured directly: a `workers` sweep from 1 to 32 plateaus
    after 2-4 workers on every CPU-bound route). `SO_REUSEPORT` has no real
    equivalent on Windows (`SO_REUSEADDR` there does not load-balance the
    same way), so `processes > 1` raises `RustRuntimeUnavailableError` there
    rather than silently falling back to single-process behavior. This is a
    multi-process launcher, not a supervisor: a child process that exits
    (crash or otherwise) is not restarted -- that is real, disclosed scope
    this function does not cover yet (see `NEXT_RELEASE_ROADMAP.md`'s
    Phase 4 for the remaining supervision/restart work).
    """
    try:
        import hayavega_native
    except ImportError as error:
        raise RustRuntimeUnavailableError("Settings.server.engine is 'rust' but hayavega_native was not built for this install. Rebuild with `cargo` on PATH (`pip install -e .` re-runs the build hook), or set Settings.server.engine to 'python' to use the standard runtime.") from error

    if processes > 1 and sys.platform == "win32":
        raise RustRuntimeUnavailableError("processes>1 needs SO_REUSEPORT for real multi-core scaling, which has no real equivalent on Windows (SO_REUSEADDR there does not load-balance the same way). Use processes=1 on Windows, or deploy on Linux for multi-process scaling.")

    # Environment-dependent, not a design gap: reached only when
    # hayavega_native is actually built (the ImportError above already
    # returned otherwise), so the pure-Python fallback-mode CI job can
    # never exercise it -- the native-extension job does, for real
    # (tests/test_rust_runtime.py and friends); excluded here so that
    # job's 100% coverage gate stays meaningful.
    application = create_application(urlpatterns, hardening=hardening, debug=debug)  # pragma: no cover
    scheme = "https" if tls_cert else "http"  # pragma: no cover
    reuse_port = processes > 1  # pragma: no cover
    print(f"Hayavega development server (Rust runtime, {processes} process(es) x {workers} workers): {scheme}://{host}:{port}/")  # pragma: no cover

    if processes > 1:  # pragma: no cover
        child_pids = [_fork_worker(application, host, port, workers, tls_cert, tls_key) for _ in range(processes - 1)]
        try:
            hayavega_native.serve_wsgi(application, host, port, workers, tls_cert, tls_key, reuse_port)
        finally:
            for pid in child_pids:
                try:
                    os.kill(pid, 15)  # SIGTERM
                except OSError:
                    pass
    else:  # pragma: no cover
        hayavega_native.serve_wsgi(application, host, port, workers, tls_cert, tls_key, reuse_port)


def _fork_worker(application, host: str, port: int, workers: int, tls_cert: str | None, tls_key: str | None) -> int:  # pragma: no cover -- Unix-only, multi-process; see serve()'s pragma rationale
    """Forks one additional worker process, which serves forever with
    `reuse_port=True` and never returns to the parent's call site."""
    import hayavega_native

    pid = os.fork()
    if pid == 0:
        try:
            hayavega_native.serve_wsgi(application, host, port, workers, tls_cert, tls_key, True)
        finally:
            os._exit(0)
    return pid
