# Security

Hardening, CSRF, rate limiting, trusted proxies, and the error-disclosure
standard every request path in Mangoo is held to.

## Secret key

```python title="config/settings.py"
import os

class Settings:
    secret_key = os.getenv("MANGOO_SECRET_KEY", "change-this-in-production")
```

**Generation.** Mangoo does not auto-generate one for you at `mango
newproject` time (unlike Django's `startproject`). The scaffolded
`secret_key` reads `MANGOO_SECRET_KEY` from the environment, falling back
to the placeholder string `"change-this-in-production"` if it's unset.
Generate a real one yourself and set it as an environment variable, never
committed to source control:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

**Validation.** Two places reject the placeholder:

- `mango diagnose` ([below](#mangopy-diagnose)) warns (or errors, once
  `debug = False`) if `secret_key` is empty or a known placeholder
  (`"change-this-in-production"`, `"changeme"`, `"secret"`).
- `mango launch`/[production validation](#production-validation) refuses
  to start a non-debug server with the same placeholder values.

**What it's used for -- and what it isn't.** Unlike Django, where
`SECRET_KEY` signs session cookies, CSRF tokens, and password-reset
tokens, Mangoo generates independent randomness via `secrets` for each of
those (`auth.create_session`, `auth.create_password_reset_token`,
`auth.create_email_verification_token`) -- `secret_key` plays no part in
any of them. The one place it's actually load-bearing is **TOTP secret
encryption at rest**:

```python
from mangoo.auth import enable_totp, authenticate

secret = enable_totp(DATABASE, "alice", secret_key=SETTINGS.secret_key)
authenticate(DATABASE, "alice", password, totp_code="123456", secret_key=SETTINGS.secret_key)
```

`enable_totp` derives a 256-bit AES key from `secret_key` via HKDF-SHA256
and encrypts the generated TOTP secret with AES-256-GCM
(`mangoo.crypto`) before storing it, using a fresh random nonce each
time. This needs `cryptography` installed (`uv add cryptography` --
kept optional, the same policy as the PostgreSQL/MySQL drivers, rather
than a mandatory dependency for every project that never enables TOTP).
Without it, or without `secret_key`, the secret is stored in plaintext
instead -- `mango diagnose` reports that fallback as an `info`-level
finding.

`mango.py enable2fa` already passes `Settings.secret_key` for you; code
that calls `enable_totp`/`authenticate`/`auth_routes` directly needs to
pass it explicitly. If a user's secret was stored encrypted, every later
`authenticate()` call for them needs the *same* `secret_key`, or the TOTP
check **fails closed** -- treated as a wrong code, never silently
skipped -- rather than accepting any code when decryption can't happen.
See [Authentication](auth.md#two-factor-authentication-totp) for the full
TOTP walkthrough.

**Rotating it.** Changing `secret_key` invalidates every TOTP secret
encrypted under the old one -- affected users need `enable2fa` run again.
It has no effect on sessions, CSRF tokens, or password-reset tokens,
since none of those are derived from it.

## The `security` section

```python title="config/settings.py"
class security:
    allowed_origins = ["https://myapp.example.com"]
    rate_limit = 120  # requests per window, per client
    rate_window_seconds = 60
    security_headers = True  # X-Frame-Options, X-Content-Type-Options, etc.
    force_https = True  # required in production
    trust_proxy_headers = True  # only behind a real, trusted reverse proxy
    trusted_proxy_ips = ["10.0.0.0/8"]
    max_request_bytes = 1_048_576
```

`HardeningOptions.from_settings(allowed_hosts=..., security=...)` builds
the full set consumed by both `mangoo.runtime.create_application` and
`mangoo.asgi.create_asgi_application` &mdash; identical enforcement on
either transport.

## `mango.py diagnose`

```bash
python mango.py diagnose
```

Proactively reports unsafe settings (`Settings.debug` left on, a placeholder
`Settings.secret_key`, a wildcard or empty `Settings.allowed_hosts`, `force_https`/
`secure_cookies` off), deprecated Pulse-era API usage, and pending
migrations &mdash; before you discover them at runtime or in production.
Severity scales with whether `Settings.debug` is on.

## Production validation

```bash
python mango.py launch  # validates production settings before starting
```

Fails with actionable errors for unsafe configuration &mdash; a missing
`Settings.secret_key`, `Settings.debug = True` in what looks like a production run,
`Settings.security.force_https` not set. See `validate_production()` in
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
default), the same trade-off many frameworks' debug-mode 404 pages make.
Never exposes anything beyond your own app's route table.

## Audit log

Authentication and admin actions are recorded to a persistent audit log
(`mangoo_audit_events`) &mdash; field names that changed, never the values,
for every recorded update. See [Authentication](auth.md#audit-events)
and [Admin](admin.md).

## Security review checklist

A living checklist covering sessions, CSRF, CORS, proxy trust, headers,
and rate limits, each item linked to a real test or a documented
operational control &mdash; see `SECURITY_REVIEW_CHECKLIST.md` in the
repository.
