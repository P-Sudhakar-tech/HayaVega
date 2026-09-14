# Security

Hardening, CSRF, rate limiting, trusted proxies, and the error-disclosure
standard every request path in Mangoo is held to.

## `SECURITY` settings

```python title="project/settings.py"
SECURITY = {
    "ALLOWED_ORIGINS": ["https://myapp.example.com"],
    "RATE_LIMIT": 120,  # requests per window, per client
    "RATE_WINDOW_SECONDS": 60,
    "SECURITY_HEADERS": True,  # X-Frame-Options, X-Content-Type-Options, etc.
    "FORCE_HTTPS": True,  # required in production
    "TRUST_PROXY_HEADERS": True,  # only behind a real, trusted reverse proxy
    "TRUSTED_PROXY_IPS": ["10.0.0.0/8"],
    "MAX_REQUEST_BYTES": 1_048_576,
}
```

`HardeningOptions.from_settings(allowed_hosts=..., security=...)` builds
the full set consumed by both `mangoo.runtime.create_application` and
`mangoo.asgi.create_asgi_application` &mdash; identical enforcement on
either transport.

## `manage.py diagnose`

```bash
python manage.py diagnose
```

Proactively reports unsafe settings (`DEBUG` left on, a placeholder
`SECRET_KEY`, a wildcard or empty `ALLOWED_HOSTS`, `FORCE_HTTPS`/
`SECURE_COOKIES` off), deprecated Pulse-era API usage, and pending
migrations &mdash; before you discover them at runtime or in production.
Severity scales with whether `DEBUG` is on.

## Production validation

```bash
python manage.py launch  # validates production settings before starting
```

Fails with actionable errors for unsafe configuration &mdash; a missing
`SECRET_KEY`, `DEBUG=True` in what looks like a production run,
`FORCE_HTTPS` not set. See `validate_production()` in
`mangoo.production`.

## CSRF

```python
from mangoo.security import csrf_protect


@csrf_protect
def submit(request): ...
```

Accepts either an `X-Csrf-Token` header or a `csrf_token` form field &mdash;
covers both JSON/API clients and traditional server-rendered `<form>`
submissions with one decorator.

## Rate limiting

Bounded-memory, per-client rate limiting with a documented Redis/edge
replacement path for multi-instance deployments &mdash; see
`RATE_LIMIT`/`RATE_WINDOW_SECONDS`/`RATE_LIMIT_MAX_CLIENTS` above.

## The error-disclosure standard

An unhandled exception on any request path &mdash; REST, GraphQL, template
rendering, routing &mdash; never leaks its raw message to an anonymous
client. Only `error_type` (e.g. `"RuntimeError"`) is exposed in the
response; the full exception with traceback is always logged
server-side. This closed a real, found gap: a database connection
failure's message routinely embeds host, port, and username, which would
otherwise go straight to any client that triggers one.

## Debug-mode 404s

`create_application(..., debug=True)` lists every registered route
pattern on a 404 &mdash; computed once at application-build time (free
when a route matches, free in production where `debug=False` is the
default), the same trade-off Django's own `DEBUG` 404 page makes. Never
exposes anything beyond your own app's route table.

## Audit log

Authentication and admin actions are recorded to a persistent audit log
(`mangoo_audit_events`) &mdash; field names that changed, never the values,
for every recorded update. See [Authentication](auth.md#audit-events)
and [Admin](admin.md).

## Security review checklist

A living checklist covering sessions, CSRF, CORS, proxy trust, headers,
and rate limits, each item linked to a real test or a documented
operational control &mdash; see
[`SECURITY_REVIEW_CHECKLIST.md`](https://github.com/P-Sudhakar-tech/mangoo-framework/blob/main/SECURITY_REVIEW_CHECKLIST.md)
in the repository.
