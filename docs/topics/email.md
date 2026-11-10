# Email

Three mail backends behind one `send()` interface -- console, file, and
real SMTP (stdlib `smtplib`, no new dependency).

## Sending a message directly

```python
from hayavega.mail import ConsoleEmailBackend, EmailMessage, send_mail

backend = ConsoleEmailBackend()  # or FileEmailBackend(...) / SMTPEmailBackend(...)
send_mail(EmailMessage(to=("alice@example.com",), subject="Welcome", body="Thanks for signing up."), backend)
```

`send_mail(message, backend)` takes the backend explicitly -- no hidden
global-settings lookup, the same pattern `HardeningOptions`/`SignalBus`
already use: build it once, pass it where it's needed.

## Configuring a backend from settings

```python title="config/settings.py"
class Settings:
    class email:
        backend = "smtp"  # "console" (default), "file", or "smtp"
        host = "smtp.example.com"
        port = 587
        username = "notifications@example.com"
        password = os.getenv("SMTP_PASSWORD")
        use_tls = True
```

```python
from hayavega.conf import load_settings
from hayavega.mail import backend_from_settings

settings = load_settings()
backend = backend_from_settings(settings.email)
```

`"file"` needs a `directory`; `"smtp"` needs a `host` (`port`/
`username`/`password`/`use_tls` are all optional, defaulting to `587`/
none/none/`True`).

## Backends

| Backend | Behavior |
|---|---|
| `ConsoleEmailBackend` | Logs the message -- the same thing `auth_api.log_mail_notifier` already does as the default `notify` |
| `FileEmailBackend(directory)` | Writes one small text file per message -- useful for local development without a real provider |
| `SMTPEmailBackend(host, ...)` | Real SMTP via `smtplib`, with `STARTTLS` and login when configured |

Any object with a `send(message)` method works -- there's no base
class to subclass, the same duck-typed contract `hayavega.middleware`
uses for its factories.

## Wiring real mail into `auth_routes`

`auth_routes(..., notify=...)` already accepts a `notify(kind, user,
token)` callable for password-reset and email-verification messages;
`mail_notifier()` adapts any backend into that shape:

```python
from hayavega.auth_api import auth_routes
from hayavega.mail import backend_from_settings, mail_notifier

backend = backend_from_settings(settings.email)

urlpatterns = [
    *auth_routes(DATABASE, secret_key=settings.secret_key, notify=mail_notifier(backend)),
]
```

Without this, `auth_routes` falls back to its default `notify`, which
only logs the token -- fine for local development, not for a real
password-reset flow.
