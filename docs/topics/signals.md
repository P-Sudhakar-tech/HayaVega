# Signals

An opt-in, in-process event bus &mdash; connect/disconnect subscribers,
deterministic delivery order, error isolation between subscribers, and
always-async delivery (no sync/async choice for callers to get wrong).

## Wiring a bus in

```python
from mangoo.addons.signals import SignalBus
from mangoo.runtime import create_application

bus = SignalBus()
bus.connect("request.finished", lambda event: print(event.data))

application = create_application(urlpatterns, signals=bus)
```

`create_application`, `auth_routes`, `admin_routes`, and
`migrations.apply` all accept `signals=` to emit request, authentication,
administration, and migration events.

## Built-in events

`request.started`/`request.finished`, `auth.login_succeeded`,
`auth.password_reset_requested`/`completed`, `auth.session_rotated`,
`admin.action`, `migration.applied`, and more &mdash; every one documented
alongside the subsystem that emits it (see
[Authentication](auth.md#audit-events)).

## Delivery

Always async &mdash; delivery never blocks the publisher. A Rust-native
worker-pool dispatcher (`mangoo_native.SignalDispatcher`) is used when
the native extension is built; a behavior-compatible Python
`ThreadPoolExecutor` fallback is used otherwise. A subscriber that raises
is logged and isolated &mdash; it never stops delivery to later
subscribers or crashes the publisher.

## Sensitive-key rejection

An event's `data` dict is checked against a sensitive-key denylist
(`password`, `token`, `secret`, ...) at publish time &mdash; publishing one
raises, catching an accidental credential leak into the event bus before
it reaches any subscriber, rather than relying on every subscriber to be
careful.

## A note on the name

This was originally shipped as **Pulse**. `mangoo.addons.pulse`/`pulse=`
still work as deprecated aliases (`PulseBus is SignalBus`, identity-equal,
not a copy) with a single `DeprecationWarning` on first use &mdash; no hard
break for existing code.
