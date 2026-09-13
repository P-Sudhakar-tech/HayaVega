# Mangoo production deployment guide

## Running the application

`mango launch` / `python manage.py launch` uses `wsgiref.simple_server`,
which is explicitly single-threaded and dev-only -- do not put it behind
real traffic. Every scaffolded project (`mango newproject`) ships a
`project/wsgi.py` exposing a standard WSGI `application` object; point a
real, multi-worker server at that instead:

```bash
# Linux/macOS: gunicorn (the conventional choice for a sync WSGI app)
gunicorn project.wsgi:application --workers 5 --bind 0.0.0.0:8000

# Any platform, including Windows: waitress
# (gunicorn cannot run on Windows at all -- it imports fcntl, a POSIX-only
# module, and fails before it can even start)
waitress-serve --port=8000 --threads=16 project.wsgi:application
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
model can't overlap as cheaply. `mango newproject` also scaffolds
`project/asgi.py`, exposing the same kind of standard `application` object
but built with `mangoo.asgi.create_asgi_application()` instead of
`mangoo.runtime.create_application()`:

```bash
uvicorn project.asgi:application --workers 4 --host 0.0.0.0 --port 8000
hypercorn project.asgi:application --workers 4 --bind 0.0.0.0:8000
```

Views may be `async def` (awaited directly on the event loop) or ordinary
synchronous functions (run via `asyncio.to_thread`, so every existing sync
view -- and every existing `api_route`/`csrf_protect`/`login_required`
view -- keeps working completely unmodified under ASGI with no migration
required). [`benchmarks/README.md`](benchmarks/README.md#asgi-vs-fastapi)
benchmarks Mangoo's ASGI transport against FastAPI on the same server
(`uvicorn`) before recommending it for anything: it wins three of the four
scenarios there, though none of those scenarios exercise the concurrent
I/O that is the actual reason to choose ASGI over WSGI in a real project
with that shape of workload.

## Before deployment

1. Set a unique `MANGOO_SECRET_KEY`; never use the scaffold default.
2. Set `DEBUG = False`, explicit `ALLOWED_HOSTS`, and `SECURITY['FORCE_HTTPS'] = True`.
3. Set `SECURITY['SECURE_COOKIES'] = True` and configure `TRUSTED_PROXY_IPS` when a reverse proxy sends forwarded headers.
4. Apply and verify migrations with `python manage.py applymigrations` and `python manage.py migrationstatus`.
5. Configure application health probes for `/healthz` and `/readyz`.
6. If you mounted `metrics_routes`, point a Prometheus scrape config at `/metrics`.

## Reverse proxy and HTTPS

Terminate TLS at a trusted proxy and forward `Host`, `X-Forwarded-For`, and
`X-Forwarded-Proto`. Only list the proxy's address or CIDR in
`SECURITY['TRUSTED_PROXY_IPS']`; do not trust forwarded headers from arbitrary
internet clients. The application redirects HTTP to HTTPS and retains query
strings.

## Migration deployment and rollback

Take a verified database backup before applying migrations. Mangoo migrations
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
events, and `mangoo_audit_events`. The built-in rate limiter is process-local;
use a reverse proxy or Redis-backed limiter when running more than one instance.
Request counts, latency, and error rate are available at `/metrics` when the
application opts in with a `MetricsRegistry`; see [README.md](README.md#observability).
Metrics are process-local like the rate limiter, so scrape every instance
individually rather than expecting an aggregate across a fleet.
