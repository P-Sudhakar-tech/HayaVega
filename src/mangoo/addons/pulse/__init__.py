"""Deprecated: renamed to ``mangoo.addons.signals``.

Import from ``mangoo.addons.signals`` instead. This module re-exports the
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
    "mangoo.addons.pulse is deprecated; import from mangoo.addons.signals instead (PulseBus -> SignalBus, PulseEvent -> SignalEvent, PulseSubscriber -> SignalSubscriber).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["BUILTIN_EVENTS", "PulseBus", "PulseEvent", "PulseSubscriber"]
