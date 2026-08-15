# Mangoo Pulse (planned)

Pulse is Mangoo's future opt-in event system for application lifecycle hooks.
It is not wired into the runtime yet, so installing this source package does
not add handlers, latency, or failure modes to existing applications.

## Proposed usage

```python
from mangoo.addons.pulse import PulseEvent

def on_login(event: PulseEvent):
    # enqueue a notification; do not block request handling
    pass
```

## Built-in names

`request.started`, `request.finished`, `auth.login_succeeded`,
`auth.login_failed`, `auth.session_created`, `auth.session_revoked`,
`admin.action`, and `migration.applied` are reserved names.

## Integration gates

Before activation, Pulse must provide a registry with explicit connect and
disconnect operations; immutable event payloads; deterministic subscriber
ordering; synchronous error isolation; optional asynchronous delivery; and
tests that guarantee no passwords, session tokens, request bodies, or headers
are exposed to subscribers. Handlers must not run inside database transactions
unless that event explicitly documents transactional semantics.
