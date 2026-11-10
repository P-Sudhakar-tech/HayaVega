# Hayavega production deployment guide

## Running the application

`hayavega launch` / `python hayavega.py launch` uses `wsgiref.simple_server`,
which is explicitly single-threaded and dev-only -- do not put it behind
real traffic. Every scaffolded project (`hayavega newproject`) ships a
`config/wsgi.py` exposing a standard WSGI `application` object; point a
real, multi-worker server at that instead:

```bash
# Linux/macOS: gunicorn (the conventional choice for a sync WSGI app)
gunicorn config.wsgi:application --workers 5 --bind 0.0.0.0:8000

# Any platform, including Windows: waitress
# (gunicorn cannot run on Windows at all -- it imports fcntl, a POSIX-only
# module, and fails before it can even start)
waitress-serve --port=8000 --threads=16 config.wsgi:application
```

`create_application()`'s return value is a plain WSGI callable with no
special requirements -- this was verified directly against a real
multi-threaded `waitress` server (and against the same object with a real
`gunicorn`-style deployment pattern) as part of
[`benchmarks/`](benchmarks/README.md), not just assumed from "WSGI is
WSGI." That benchmark suite also shows concretely why this matters: a
production server easily handles 1,000+ req/s where the single-threaded
dev server would serialize every request behind the one it's currently
handling.

**Sizing workers/threads:** for `gunicorn`, the common starting heuristic is
`2 * cpu_count + 1` worker *processes* (each handling one request at a
time, so this scales with concurrent request volume, not raw CPU work).
For `waitress`, `--threads` controls a single process's *thread* pool;
since Python's GIL limits how much CPU-bound work truly runs in parallel,
more threads mainly help I/O-bound waiting (database queries, downstream
HTTP calls) overlap -- start around 8-16 and measure, rather than assuming
higher is always better. `benchmarks/run.py` demonstrates exactly this
kind of measurement if you want to reproduce it against your own workload.

### ASGI: only if a view needs `async def`

WSGI above stays the default; nothing about it requires ASGI. Reach for
ASGI only when a view needs to `await` concurrent I/O -- calling another
service, an async database driver -- that WSGI's one-thread-per-request
model can't overlap as cheaply. `hayavega newproject` also scaffolds
`config/asgi.py`, exposing the same kind of standard `application` object
but built with `hayavega.asgi.create_asgi_application()` instead of
`hayavega.runtime.create_application()`:

```bash
uvicorn config.asgi:application --workers 4 --host 0.0.0.0 --port 8000
hypercorn config.asgi:application --workers 4 --bind 0.0.0.0:8000
```

Views may be `async def` (awaited directly on the event loop) or ordinary
synchronous functions (run via a dedicated worker-thread pool --
`create_asgi_application(..., max_sync_workers=16)` by default, matching
this guide's own `waitress --threads=16` -- so every existing sync view --
and every existing `api_route`/`csrf_protect`/`login_required`
view -- keeps working completely unmodified under ASGI with no migration
required). [`benchmarks/README.md`](benchmarks/README.md#asgi-vs-fastapi)
benchmarks Hayavega's ASGI transport against FastAPI on the same server
(`uvicorn`) before recommending it for anything: after fixing the default
`asyncio.to_thread` event-loop-wide executor being sized for occasional
CPU-bound offloading (`min(32, os.cpu_count() + 4)`, a real bottleneck
under real concurrency) rather than a whole application's I/O-bound sync
views, Hayavega (ASGI) wins or ties FastAPI on every scenario, though none of
those scenarios exercise the concurrent I/O that is the actual reason to
choose ASGI over WSGI in a real project
with that shape of workload.

### Rust runtime: an opt-in, settings-driven native WSGI server

`Settings.server.engine = 'rust'` in a project's settings module switches
`hayavega launch` (and `python hayavega.py launch`) from the default `wsgiref`
dev server to `hayavega_native`'s own WSGI-compatible HTTP/1.1 server --
still development tooling, not a production server recommendation of its
own (see the gunicorn/waitress guidance above for that), but a real,
benchmarked alternative for local development and load testing. Requires
`hayavega_native` to have been built (`cargo` on `PATH` during
`pip install`/`pip install -e .`, the same requirement Signals' Rust
dispatcher already has) -- `hayavega launch` reports a clear, actionable
error rather than silently falling back if it wasn't.

```python
# config/settings.py, inside class Settings:
class server:
    host = "127.0.0.1"
    port = 8000
    engine = "rust"  # default: "python"
```

**Scope, disclosed rather than oversold:** this is a synchronous,
fixed-thread-pool server (`workers=` on `hayavega.rust_runtime.serve`,
default 8) -- the same concurrency model `waitress` already uses -- not a
`tokio`/`hyper` async runtime. CPython's GIL means only one thread ever
executes Python view code at a time regardless of the I/O layer, so async
I/O would not let more Python code run concurrently than a thread pool
already does; Rust's role here is the same narrow one every other native
component in this codebase plays (own socket accept, HTTP/1.1 parsing --
including real keep-alive support -- and response writing around
unchanged Python request-handling code via the standard WSGI contract).
Full detail, including two real bugs found and fixed while benchmarking
it (`TCP_NODELAY`, and a Windows ephemeral-port-exhaustion failure mode
without keep-alive -- the same failure mode already documented in
[`benchmarks/README.md`'s "A real gotcha this suite ran into"](benchmarks/README.md#a-real-gotcha-this-suite-ran-into-and-why-the-django-app-looks-the-way-it-does)
for the Django benchmark target), in
[`benchmarks/README.md`](benchmarks/README.md#the-rust-runtime-vs-the-python-runtime-waitress).
A dedicated hardening pass afterward found and fixed five more real
issues, three of them exploitable (request smuggling, response
splitting) -- see
[`benchmarks/README.md`'s hardening section](benchmarks/README.md#hardening-pass-the-known-failure-modes-of-a-hand-written-http-server-found-and-fixed).

**Optional TLS**: `Settings.server.tls_cert`/`Settings.server.tls_key` (PEM file
paths) serve the application over HTTPS instead of plain HTTP, using
`rustls` -- both settings are only valid with `Settings.server.engine = 'rust'`
(rejected otherwise, rather than silently serving plaintext where HTTPS
was expected). Benchmarked overhead is modest (roughly 0-12% depending on
scenario, since the TLS handshake cost is paid once per connection and
amortized by keep-alive, not paid per request) -- see
[`benchmarks/README.md`'s TLS section](benchmarks/README.md#optional-tls-termination-real-https-modest-overhead)
for the full numbers. HTTP/2 remains explicitly out of scope: it is built
around multiplexing many streams over one connection, which does not fit
this server's synchronous thread-per-connection model, and its main
benefit (parallel loading of many small browser resources) does not apply
to a backend API server the way it does to a web page. This TLS support
is for local HTTPS development/testing, not a production-TLS-termination
recommendation -- a real deployment still terminates TLS at a reverse
proxy (nginx, a cloud load balancer), the same as the gunicorn/waitress
guidance above.

```python
# config/settings.py, inside class Settings:
class server:
    host = "127.0.0.1"
    port = 8443
    engine = "rust"
    tls_cert = "cert.pem"
    tls_key = "key.pem"
```

## Before deployment

1. Set a unique `HAYAVEGA_SECRET_KEY`; never use the scaffold default.
2. Set `Settings.debug = False`, explicit `Settings.allowed_hosts`, and `Settings.security.force_https = True`.
3. Set `Settings.security.secure_cookies = True` and configure `Settings.security.trusted_proxy_ips` when a reverse proxy sends forwarded headers.
4. Apply and verify migrations with `python hayavega.py applymigrations` and `python hayavega.py migrationstatus`.
5. Configure application health probes for `/healthz` and `/readyz`.
6. If you mounted `metrics_routes`, point a Prometheus scrape config at `/metrics`.

## Reverse proxy and HTTPS

Terminate TLS at a trusted proxy and forward `Host`, `X-Forwarded-For`, and
`X-Forwarded-Proto`. Only list the proxy's address or CIDR in
`Settings.security.trusted_proxy_ips`; do not trust forwarded headers from arbitrary
internet clients. The application redirects HTTP to HTTPS and retains query
strings.

## Migration deployment and rollback

Take a verified database backup before applying migrations. Hayavega migrations
are forward-only SQL files: if a deployment must be reverted, deploy the prior
application version and restore the verified backup, or write a reviewed
compensating migration. Never edit an applied migration file because checksum
validation will stop deployment.

## Backup and restore drill

For SQLite, stop writes, copy the database using SQLite's backup mechanism (or
an application-consistent filesystem snapshot), and test opening the copy.
For PostgreSQL use `pg_dump`/`pg_restore`; for MySQL/MariaDB use `mysqldump`
and a restore into an isolated database. At least once per release cycle,
restore a backup to an isolated environment, run `migrationstatus`, and verify
`/readyz`.

## Operations

Monitor the request ID in application logs, readiness failures, rate-limit
events, and `hayavega_audit_events`. By default the built-in rate limiter is
process-local, so a fleet of N instances behind a load balancer independently
enforces the configured limit N times over. To enforce one combined limit
across every instance, opt into the Redis-backed limiter by setting
`RATE_LIMIT_BACKEND = "redis"` and `REDIS_URL = "redis://host:6379/0"` in the
`security` settings; this requires `uv add redis`. The default stays the
in-process limiter -- this is opt-in and does not change behavior for
existing deployments that don't set `RATE_LIMIT_BACKEND`.
Request counts, latency, and error rate are available at `/metrics` when the
application opts in with a `MetricsRegistry`; see [README.md](README.md#observability).
Metrics are process-local like the rate limiter, so scrape every instance
individually rather than expecting an aggregate across a fleet.
