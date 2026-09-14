# Deployment

## WSGI: gunicorn or waitress

`mango launch`'s `wsgiref` dev server is explicitly single-threaded and
dev-only &mdash; never put it behind real traffic. Every scaffolded project
ships `project/wsgi.py`, a standard WSGI `application` object:

```bash
# Linux/macOS
gunicorn project.wsgi:application --workers 5 --bind 0.0.0.0:8000

# Any platform, including Windows (gunicorn imports fcntl, POSIX-only)
waitress-serve --port=8000 --threads=16 project.wsgi:application
```

`create_application()`'s return value is a plain WSGI callable, verified
directly against a real multi-threaded server as part of this project's
own [benchmark suite](../performance.md) &mdash; not assumed from "WSGI is
WSGI."

**Sizing:** for gunicorn, `2 * cpu_count + 1` worker *processes* is the
common starting heuristic. For waitress, `--threads` controls a single
process's thread pool; since the GIL limits true CPU-bound parallelism,
more threads mainly help I/O-bound waiting overlap &mdash; start around
8&ndash;16 and measure against your own workload rather than assuming
higher is always better.

## ASGI: only if a view needs `async def`

```bash
uvicorn project.asgi:application --workers 4 --host 0.0.0.0 --port 8000
hypercorn project.asgi:application --workers 4 --bind 0.0.0.0:8000
```

See [ASGI](asgi.md) for when to reach for this over WSGI.

## The Rust runtime

`SERVER['ENGINE'] = 'rust'` is development/load-testing tooling, not a
production-server recommendation of its own &mdash; see
[Rust runtime](rust-runtime.md) for the full scope and its benchmarked
result against `waitress`.

## Reverse proxy and HTTPS

A real deployment terminates TLS and HTTP/2 at a reverse proxy (nginx,
a cloud load balancer) in front of whichever application server you
chose above &mdash; `TRUST_PROXY_HEADERS`/`TRUSTED_PROXY_IPS` in
[`SECURITY`](security.md) tell Mangoo to trust that proxy's
`X-Forwarded-*` headers for `request.is_secure` and `REMOTE_ADDR`.

## Before deployment

- [ ] `SECRET_KEY` is a real, environment-provided secret &mdash; not the
      scaffolded placeholder
- [ ] `DEBUG = False`
- [ ] `ALLOWED_HOSTS` is your real domain list, not empty or `["*"]`
- [ ] `SECURITY["FORCE_HTTPS"] = True`
- [ ] `python manage.py diagnose` reports nothing at `error` severity
- [ ] `python manage.py migrationstatus` shows nothing pending

## Migration deployment and rollback

Migrations are transactional and checksummed &mdash; a failed migration
leaves no partial schema change where the database supports
transactions. Forward-only by design; a rollback is a new, reviewed
migration, not an automatic reversal.

## Backup and restore

Standard database-level backup/restore for your chosen engine
(`pg_dump`/`pg_restore` for PostgreSQL, `mysqldump` for MySQL/MariaDB, a
file copy for SQLite) &mdash; Mangoo doesn't reinvent this layer. Test a
real restore drill before you need one for real.

## Health and readiness

Built-in health/readiness endpoints check real database connectivity,
not just "the process is running" &mdash; wire them into your
orchestrator's liveness/readiness probes.
