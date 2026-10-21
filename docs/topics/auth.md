# Authentication

A complete session-based auth system: registration, login, password
reset, email verification, 2FA (TOTP), session listing/rotation/
revoke-all, and audit events for every step &mdash; all pluggable into
your own routes or used as the pre-wired `auth_routes()` endpoints.

## Pre-wired routes

```python
from mangoo.auth_api import auth_routes

urlpatterns = [
    *auth_routes(DATABASE, secret_key=SECRET_KEY),
]
```

This wires up `POST /api/auth/login`, `POST /api/auth/logout`,
`POST /api/auth/password-reset/{request,confirm}`,
`GET /api/auth/sessions`, `POST /api/auth/sessions/revoke-others`, and
more &mdash; all fully described in the generated OpenAPI document,
including the shared `{"detail": string}` error envelope. See
[REST & OpenAPI](rest-api.md).

## Users and sessions, directly

```python
from mangoo.auth import create_user, authenticate, create_session, authenticate_session

user = create_user(DATABASE, "alice", "alice@example.com", "Correct-Horse-7!")
authenticated = authenticate(DATABASE, "alice", "Correct-Horse-7!")
token = create_session(DATABASE, authenticated)
still_valid = authenticate_session(DATABASE, token)
```

Passwords are validated (`validate_password`) and hashed
(`hash_password`/`verify_password`) before storage; sessions are opaque
tokens, never a value that encodes the user directly.

## Password policy

The default (`validate_password`: 12+ characters, lower/upper/digit/
symbol) isn't hardcoded into `create_user` -- it's just the default
`validators` list (`[validate_password]`). Pass your own to
`create_user`/`create_superuser`/`reset_password` to replace it
entirely:

```python
from mangoo.auth import common_password_validator, create_user, min_length_validator

create_user(
    DATABASE,
    "alice",
    "alice@example.com",
    "a-different-policy-pw",
    validators=[min_length_validator(8), common_password_validator(["password123", "qwerty123456"])],
)
```

Each validator is `callable(password) -> None`, raising `ValueError`
with a message to reject it; the first rejection wins (validators
after it never run). Two ready-made ones ship alongside the default:
`min_length_validator(n)` (a relaxed length-only check) and
`common_password_validator(denylist)` (case-insensitive deny-list
match -- pair it with a length/complexity validator, since alone it
enforces neither).

`mango.py createadmin` reads `Settings.password_validators` (dotted
paths to already-built validators, resolved the same way
`job_handlers` is) when set, falling back to the default otherwise:

```python title="config/settings.py"
class Settings:
    password_validators = ["myapp.validators.relaxed_policy"]
```

```python title="myapp/validators.py"
from mangoo.auth import min_length_validator

relaxed_policy = min_length_validator(8)
```

A dotted path must point at an already-built validator (a plain
callable) -- not a factory call like `min_length_validator(8)`
written inline, since only a bare attribute can be resolved by name.

`reset_password(..., validators=...)` validates the new password
*before* consuming the single-use reset token -- a rejected password
never burns the user's reset link, which still works on a later,
valid attempt.

## Gating a view

```python
from mangoo.security import login_required, permission_required, superuser_required, email_verified_required


@login_required(DATABASE)
def dashboard(request):
    return Response(f"Welcome, {request.user.username}")


@superuser_required(DATABASE)
def admin_panel(request): ...


@permission_required(DATABASE, lambda user: user.is_staff)
def staff_only(request): ...


@email_verified_required(DATABASE)
def verified_only(request): ...
```

`permission_required(database, predicate)` is the general form &mdash;
`login_required`/`superuser_required`/`email_verified_required` are all
built on it with a specific predicate. Every one of these composes
correctly with `async def` views under [ASGI](asgi.md).

## Groups & permissions

A lighter analog of Django's `Group`/`Permission` models: permissions
are free-form string codenames (e.g. `"blog.add_post"`), not tied to a
`contenttypes` framework Mangoo doesn't have. Group a set of codenames
once, assign users to as many groups as needed, and check membership at
authorization time.

```python
from mangoo.auth import add_user_to_group, create_group, user_permissions
from mangoo.security import has_permission_required

editors = create_group(DATABASE, "editors", ["blog.add_post", "blog.change_post"])
add_user_to_group(DATABASE, alice, editors)

user_permissions(DATABASE, alice)  # {"blog.add_post", "blog.change_post"}


@has_permission_required(DATABASE, "blog.add_post")
def create_post(request): ...
```

`user_permissions` returns the union of every group a user belongs to
-- it does not special-case `is_superuser`. `has_permission_required`
does: a superuser always passes, the same bypass `superuser_required`
already gives every other permission check in this codebase, kept
consistent rather than making groups a special case.

## Password reset and email verification

```python
from mangoo.auth import create_password_reset_token, reset_password, create_email_verification_token, verify_email

token = create_password_reset_token(DATABASE, user)  # 1 hour, single-use
reset_password(DATABASE, token, "New-Password-9!")  # revokes every existing session

verify_token = create_email_verification_token(DATABASE, user)  # 24 hours, single-use
verify_email(DATABASE, verify_token)
```

Tokens are stored only as a SHA-256 hash, never in plaintext, and are
single-use &mdash; a reused or expired token is rejected. Mail delivery is
pluggable via a `notify(kind, user, token)` callable passed to
`auth_routes(...)`; the default only logs the token, so the flow works
without a mail provider configured during development. See
[Email](email.md) for real console/file/SMTP backends and
`mail_notifier()`, which adapts one into this exact `notify` shape.

## Two-factor authentication (TOTP)

```python
from mangoo.auth import enable_totp, verify_totp

secret = enable_totp(DATABASE, "alice", secret_key=SECRET_KEY)  # returns the secret to show as a QR code
authenticate(DATABASE, "alice", password, totp_code="123456", secret_key=SECRET_KEY)
```

`secret_key` (pass `Settings.secret_key`) is optional but recommended: when
given, and `cryptography` is installed (`uv add cryptography` -- kept
optional, like the PostgreSQL/MySQL drivers, rather than a mandatory
dependency for every project), the stored TOTP secret is encrypted with a
key derived from `secret_key`, not stored in plaintext. Without it, the
secret is stored as-is -- `mango diagnose` reports that fallback. If a
user's secret was stored encrypted, every later `authenticate()` call for
them needs the *same* `secret_key` or the TOTP check fails closed (treated
as wrong, never silently skipped) rather than accepting any code.

## Sessions: listing, rotation, revoke-all

```python
from mangoo.auth import list_sessions, rotate_session, revoke_all_sessions

sessions = list_sessions(DATABASE, user)  # id/timestamps only, never a token
new_token = rotate_session(DATABASE, old_token)  # fresh identifier, same session
revoke_all_sessions(DATABASE, user, except_token=current_token)
```

## Audit events

Every step above emits a persistent audit event
(`auth.password_reset_requested`/`completed`,
`auth.email_verification_requested`, `auth.session_rotated`,
`auth.sessions_revoked_all`, ...) &mdash; see [Signals](signals.md) for how
to subscribe to these in-process, and [Security](security.md) for the
audit log's own storage guarantees (field names, never values, for
changed data).

## CSRF

```python
from mangoo.security import csrf_protect


@csrf_protect
def submit_form(request): ...
```

Accepts either an `X-Csrf-Token` header (API/JSON clients) or a
`csrf_token` form field (server-rendered `<form>` submissions) &mdash; the
same decorator covers both without extra wiring.
