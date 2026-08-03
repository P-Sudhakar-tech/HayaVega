# Mangoo production deployment guide

## Before deployment

1. Set a unique `MANGOO_SECRET_KEY`; never use the scaffold default.
2. Set `DEBUG = False`, explicit `ALLOWED_HOSTS`, and `SECURITY['FORCE_HTTPS'] = True`.
3. Set `SECURITY['SECURE_COOKIES'] = True` and configure `TRUSTED_PROXY_IPS` when a reverse proxy sends forwarded headers.
4. Apply and verify migrations with `python manage.py migrate` and `python manage.py showmigrations`.
5. Configure application health probes for `/healthz` and `/readyz`.

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
restore a backup to an isolated environment, run `showmigrations`, and verify
`/readyz`.

## Operations

Monitor the request ID in application logs, readiness failures, rate-limit
events, and `mangoo_audit_events`. The built-in rate limiter is process-local;
use a reverse proxy or Redis-backed limiter when running more than one instance.
