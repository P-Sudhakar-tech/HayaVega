"""Deprecated: renamed to ``hayavega.addons.signals``.

Import from ``hayavega.addons.signals`` instead. This module re-exports the
same names under their old Pulse-branded aliases (``PulseBus`` ==
``SignalBus``, ``PulseEvent`` == ``SignalEvent``) so existing code keeps
working, and will be removed in a future release.
"""

import warnings

from ..signals import BUILTIN_EVENTS
from ..signals import SignalBus as PulseBus
from ..signals import SignalEvent as PulseEvent
from ..signals import SignalSubscriber as PulseSubscriber

warnings.warn(
    "hayavega.addons.pulse is deprecated; import from hayavega.addons.signals instead (PulseBus -> SignalBus, PulseEvent -> SignalEvent, PulseSubscriber -> SignalSubscriber).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["BUILTIN_EVENTS", "PulseBus", "PulseEvent", "PulseSubscriber"]
