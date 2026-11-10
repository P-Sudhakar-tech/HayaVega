"""Hayavega Signals: opt-in, in-process application event hooks.

Formerly shipped as "Pulse" -- ``hayavega.addons.pulse`` still works as a
deprecated alias (``PulseBus`` == ``SignalBus``, ``PulseEvent`` ==
``SignalEvent``) but will be removed in a future release. Event names
(``request.started``, ``auth.login_succeeded``, ...) are unchanged.
"""

from .contracts import BUILTIN_EVENTS, SignalEvent, SignalSubscriber
from .registry import SignalBus

__all__ = ["BUILTIN_EVENTS", "SignalBus", "SignalEvent", "SignalSubscriber"]
