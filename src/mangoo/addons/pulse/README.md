# Mangoo Pulse

Pulse is an opt-in in-process event system for application lifecycle hooks. It
does not create a global registry or wire itself into the runtime, so existing
applications add no handlers, latency, or failure modes unless they create and
use a `PulseBus`.

## Proposed usage

```python
from mangoo.addons.pulse import PulseBus

bus = PulseBus()

def on_login(event):
    # enqueue a notification; do not block request handling
    pass

bus.connect("auth.login_succeeded", on_login)
failures = bus.publish("auth.login_succeeded", actor_id=42, data={"method": "password"})
```

Subscribers run in the order connected. A failing subscriber is logged; with
`publish`/`emit` it is also returned as a `PulseDeliveryFailure` without
preventing later subscribers from receiving the event. `PulseEvent.data` is a
read-only mapping, and constructing an event with an obviously sensitive key
(`password`, `token`, `secret`, `cookie`, and similar) raises `ValueError`
instead of silently carrying it.

## Delivery semantics

- `publish`/`emit` run subscribers synchronously on the caller's thread and
  return each failure. Call them only after any surrounding database
  transaction has committed, since a subscriber must never observe a change
  that could still be rolled back.
- `publish_async`/`emit_async` hand the same subscriber list to a daemon
  background thread and return immediately without failures. Use them only
  where the caller does not need delivery confirmation, such as per-request
  lifecycle events, since a slow or failing subscriber can no longer affect
  the caller's control flow.

## Built-in names

`request.started`, `request.finished`, `auth.login_succeeded`,
`auth.login_failed`, `auth.session_created`, `auth.session_revoked`,
`admin.action`, and `migration.applied` are reserved names.

## Framework wiring

These built-in names are now emitted when an application opts in by passing a
`PulseBus` to the corresponding function; nothing changes for applications
that do not pass one:

- `mangoo.runtime.create_application(urlpatterns, pulse=bus)` publishes
  `request.started`/`request.finished` asynchronously around every request.
- `mangoo.auth_api.auth_routes(database_path, pulse=bus)` publishes
  `auth.login_succeeded`, `auth.login_failed`, `auth.session_created`, and
  `auth.session_revoked` synchronously, after the session row that produced
  them has committed.
- `mangoo.admin.admin_routes(database_path, pulse=bus)` publishes
  `admin.action` synchronously, with the specific action in `data["action"]`.
- `mangoo.migrations.apply(database, directories, pulse=bus)` publishes
  `migration.applied` synchronously, after each migration's transaction has
  committed.
