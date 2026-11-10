# Hayavega Signals

Signals is an opt-in in-process event system for application lifecycle hooks. It
does not create a global registry or wire itself into the runtime, so existing
applications add no handlers, latency, or failure modes unless they create and
use a `SignalBus`.

This add-on originally shipped as "Pulse." `hayavega.addons.pulse` still works
(`PulseBus`/`PulseEvent` are deprecated aliases for `SignalBus`/`SignalEvent`)
but raises a `DeprecationWarning` and will be removed in a future release;
event names themselves (`request.started`, `auth.login_succeeded`, ...) never
changed.

## Proposed usage

```python
from hayavega.addons.signals import SignalBus

bus = SignalBus()


def on_login(event):
    # runs on a worker thread, off the request that published the event
    pass


bus.connect("auth.login_succeeded", on_login)
bus.publish("auth.login_succeeded", actor_id=42, data={"method": "password"})
```

Subscribers run in connection order on a small persistent worker pool. A
failing subscriber is logged and does not prevent later subscribers from
receiving the event. `SignalEvent.data` is a read-only mapping, and
constructing an event with an obviously sensitive key (`password`, `token`,
`secret`, `cookie`, and similar) raises `ValueError` instead of silently
carrying it.

## Delivery semantics

There is one delivery path, not a sync/async choice for callers to make:
`publish`/`emit` always hand the event to a worker and return immediately.

- Concurrency is Rust-native when available. If the optional `hayavega_native`
  extension is built, `SignalBus` dispatches through
  `hayavega_native.SignalDispatcher`, a fixed pool of OS threads owned by Rust
  (not Python's `threading` module). Without the extension, `SignalBus` falls
  back to a behavior-compatible Python `ThreadPoolExecutor` pool of the same
  size. Either way, callers see the same interface; nothing about application
  code changes based on which one is active.
- Call `publish`/`emit` only after any surrounding database transaction has
  committed, since a subscriber must never observe a change that could still
  be rolled back. That ordering is still guaranteed -- the event is *queued*
  synchronously at the call site -- but *delivery* to subscribers happens
  later, off the caller's thread.
- Because delivery happens on another thread, a failing subscriber is logged
  (via the `hayavega.signals` logger), not raised or returned to the caller.
  `publish`/`emit` cannot tell you whether a subscriber succeeded.

## Built-in names

`request.started`, `request.finished`, `auth.login_succeeded`,
`auth.login_failed`, `auth.session_created`, `auth.session_revoked`,
`admin.action`, and `migration.applied` are reserved names.

## Framework wiring

These built-in names are emitted when an application opts in by passing a
`SignalBus` to the corresponding function; nothing changes for applications
that do not pass one. `pulse=` still works as a deprecated alias for
`signals=` on every one of these:

- `hayavega.runtime.create_application(urlpatterns, signals=bus)` publishes
  `request.started`/`request.finished` around every request.
- `hayavega.auth_api.auth_routes(database_path, signals=bus)` publishes
  `auth.login_succeeded`, `auth.login_failed`, `auth.session_created`, and
  `auth.session_revoked`, after the session row that produced them has
  committed.
- `hayavega.admin.admin_routes(database_path, signals=bus)` publishes
  `admin.action`, with the specific action in `data["action"]`.
- `hayavega.migrations.apply(database, directories, signals=bus)` publishes
  `migration.applied`, after each migration's transaction has committed.

## Building the Rust-backed dispatcher

`SignalBus` works with no extra steps using its Python fallback. To get the
Rust-native worker pool, install a Rust toolchain (`cargo` on PATH) before
running the normal install command:

```powershell
pip install -e .
```

A `hatchling` build hook (`hatch_build.py` at the repository root) detects
`cargo`, builds `crates/hayavega-python`, and bundles the resulting
`hayavega_native` extension into the same install -- no separate `maturin`
command, and no risk of it replacing the `hayavega` package, since it is now
part of the one build that produces `hayavega` rather than a second,
independently-invokable build that happens to share its name. If `cargo` is
not on PATH, or `HAYAVEGA_SKIP_NATIVE_BUILD=1` is set, the hook does nothing
and you get the pure-Python fallback, exactly as before.
