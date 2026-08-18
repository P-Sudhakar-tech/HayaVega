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
    # runs on a worker thread, off the request that published the event
    pass

bus.connect("auth.login_succeeded", on_login)
bus.publish("auth.login_succeeded", actor_id=42, data={"method": "password"})
```

Subscribers run in connection order on a small persistent worker pool. A
failing subscriber is logged and does not prevent later subscribers from
receiving the event. `PulseEvent.data` is a read-only mapping, and
constructing an event with an obviously sensitive key (`password`, `token`,
`secret`, `cookie`, and similar) raises `ValueError` instead of silently
carrying it.

## Delivery semantics

There is one delivery path, not a sync/async choice for callers to make:
`publish`/`emit` always hand the event to a worker and return immediately.

- Concurrency is Rust-native when available. If the optional `mangoo_native`
  extension is built, `PulseBus` dispatches through
  `mangoo_native.PulseDispatcher`, a fixed pool of OS threads owned by Rust
  (not Python's `threading` module). Without the extension, `PulseBus` falls
  back to a behavior-compatible Python `ThreadPoolExecutor` pool of the same
  size. Either way, callers see the same interface; nothing about application
  code changes based on which one is active.
- Call `publish`/`emit` only after any surrounding database transaction has
  committed, since a subscriber must never observe a change that could still
  be rolled back. That ordering is still guaranteed -- the event is *queued*
  synchronously at the call site -- but *delivery* to subscribers happens
  later, off the caller's thread.
- Because delivery happens on another thread, a failing subscriber is logged
  (via the `mangoo.pulse` logger), not raised or returned to the caller.
  `publish`/`emit` cannot tell you whether a subscriber succeeded.

## Built-in names

`request.started`, `request.finished`, `auth.login_succeeded`,
`auth.login_failed`, `auth.session_created`, `auth.session_revoked`,
`admin.action`, and `migration.applied` are reserved names.

## Framework wiring

These built-in names are emitted when an application opts in by passing a
`PulseBus` to the corresponding function; nothing changes for applications
that do not pass one:

- `mangoo.runtime.create_application(urlpatterns, pulse=bus)` publishes
  `request.started`/`request.finished` around every request.
- `mangoo.auth_api.auth_routes(database_path, pulse=bus)` publishes
  `auth.login_succeeded`, `auth.login_failed`, `auth.session_created`, and
  `auth.session_revoked`, after the session row that produced them has
  committed.
- `mangoo.admin.admin_routes(database_path, pulse=bus)` publishes
  `admin.action`, with the specific action in `data["action"]`.
- `mangoo.migrations.apply(database, directories, pulse=bus)` publishes
  `migration.applied`, after each migration's transaction has committed.

## Building the Rust-backed dispatcher

`PulseBus` works with no extra steps using its Python fallback. To get the
Rust-native worker pool, build the extension from the repository root:

```powershell
pip install maturin
maturin develop --manifest-path crates/mangoo-python/Cargo.toml
```

Point `maturin develop` at that crate's manifest specifically, not the
repository root's `pyproject.toml` -- both currently declare the project name
`mangoo`, and building from the root installs a wheel that only contains the
compiled extension, silently replacing the real `mangoo` Python package in
your environment. If that happens, reinstall the framework with
`pip install -e .` from the repository root.
