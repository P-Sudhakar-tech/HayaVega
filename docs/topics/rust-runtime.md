# Rust runtime

An opt-in, in-built, native WSGI server &mdash; `hayavega_native.serve_wsgi`,
written in Rust &mdash; selected entirely via settings, no separate tool to
install or run.

## Enabling it

```python title="config/settings.py"
class server:
    host = "127.0.0.1"
    port = 8000
    engine = "rust"  # default: "python"
```

```bash
python manage.py launch
```

If `hayavega_native` wasn't built for this install, `hayavega launch` reports
a clear, actionable error rather than silently falling back to the
default `wsgiref` server under a different name.

## Why it's fast

Benchmarked at **1.4&ndash;2.9&times; faster than `waitress`** across
every published scenario (`json_echo`, `db_list`, `template_render`,
`static_file`) with a 100% success rate &mdash; see
[Performance](../performance.md) for the numbers, both bugs found while
getting there (`TCP_NODELAY`, and a Windows ephemeral-port-exhaustion
failure mode without keep-alive), and the follow-up hardening pass
(request smuggling, response splitting, Slowloris, and panic-isolation
fixes, verified to cost zero measured performance).

## Scope, disclosed rather than oversold

This is a **synchronous, fixed-thread-pool** server (`workers=`, default
8) &mdash; the same concurrency model `waitress` already uses, not a
`tokio`/`hyper` async runtime. CPython's GIL means only one thread ever
executes Python view code at a time regardless of the I/O layer, so
async I/O would not let more Python code run concurrently than a thread
pool already does here. Rust's role is the same narrow one every other
native component in this codebase plays: own socket accept, HTTP/1.1
parsing (including real keep-alive), and response writing around
unchanged Python request-handling code via the standard WSGI contract.

### Using more than one CPU core: `processes=`

`workers=` controls thread count *within* one process, and the GIL caps
all of them to one at a time &mdash; measured directly: throughput
plateaus after 2&ndash;4 workers on CPU-bound routes no matter how many
more are added. To actually use more than one core, pass `processes=N`
(default 1): each process gets its own Python interpreter and its own
GIL, and all of them bind the same `host:port` via `SO_REUSEPORT`
(Linux-only &mdash; it has no real equivalent on Windows, so
`processes > 1` there raises `RustRuntimeUnavailableError` instead of
silently behaving differently):

```python title="config/settings.py"
class server:
    engine = "rust"
    workers = 8
    processes = 4  # one per CPU core is a reasonable starting point
```

This is a multi-process *launcher*, not a supervisor: a child process
that exits is not restarted. Process supervision/restart is tracked as
follow-up work, not shipped yet.

## TLS

```python title="config/settings.py"
class server:
    host = "127.0.0.1"
    port = 8443
    engine = "rust"
    tls_cert = "cert.pem"
    tls_key = "key.pem"
```

Uses `rustls` (the `ring` crypto provider). Both settings must be
provided together, and are only accepted with `Settings.server.engine = 'rust'`
&mdash; rejected under the Python engine rather than silently ignored,
since that would otherwise be a real security footgun (expecting HTTPS,
silently getting plaintext). Overhead is modest (roughly 0&ndash;12%
depending on scenario, since the TLS handshake cost is paid once per
connection and amortized by keep-alive). For local HTTPS development and
testing &mdash; a real production deployment still terminates TLS at a
reverse proxy, same as the `gunicorn`/`waitress` guidance in
[Deployment](deployment.md).

## HTTP/2: deliberately out of scope

HTTP/2 is built around multiplexing many streams over one connection &mdash;
fundamentally incompatible with this server's synchronous
thread-per-connection design without effectively building a second
scheduler inside it. Its main real-world benefit (parallel loading of
many small browser-requested resources) also doesn't transfer to a
backend API server the way it does to a browser client. A reverse proxy
remains the right place for HTTP/2, same as for TLS termination in a
real deployment.

## What this version doesn't do

No client-certificate authentication, no OCSP stapling, no certificate
hot-reload (a changed cert/key needs a restart). This remains
development/load-testing tooling, not a claim that it replaces
`gunicorn`/`waitress` for production &mdash; see
[Deployment](deployment.md) for the production guidance.
