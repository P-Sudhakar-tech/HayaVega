"""Mangoo Pulse: deferred opt-in application event hooks.

This package intentionally defines contracts only. Lifecycle emission will be
enabled in a later release after delivery ordering and error policy are tested.
"""

from .contracts import BUILTIN_EVENTS, PulseEvent, PulseSubscriber

__all__ = ["BUILTIN_EVENTS", "PulseEvent", "PulseSubscriber"]
