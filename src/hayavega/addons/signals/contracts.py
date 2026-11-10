"""Public contracts for the Hayavega Signals add-on."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "session_token",
        "access_token",
        "refresh_token",
        "secret",
        "totp_secret",
        "totp_code",
        "authorization",
        "cookie",
        "csrf_token",
    }
)


@dataclass(frozen=True)
class SignalEvent:
    """Immutable event payload; never include credentials or session tokens."""

    name: str
    actor_id: int | None = None
    data: Mapping[str, Any] | None = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Signal event names must be non-empty strings.")
        payload = dict(self.data or {})
        leaked = _SENSITIVE_KEYS.intersection(key.lower() for key in payload if isinstance(key, str))
        if leaked:
            raise ValueError(f"Signal event data must not include sensitive keys: {', '.join(sorted(leaked))}")
        object.__setattr__(self, "data", MappingProxyType(payload))


SignalSubscriber = Callable[[SignalEvent], None]


BUILTIN_EVENTS = (
    "request.started",
    "request.finished",
    "auth.login_succeeded",
    "auth.login_failed",
    "auth.session_created",
    "auth.session_revoked",
    "admin.action",
    "migration.applied",
)
