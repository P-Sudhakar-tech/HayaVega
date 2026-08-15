"""Public contracts for the future Mangoo Pulse add-on."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PulseEvent:
    """Immutable event payload; never include credentials or session tokens."""
    name: str
    actor_id: int | None = None
    data: dict[str, Any] | None = None


PulseSubscriber = Callable[[PulseEvent], None]


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
