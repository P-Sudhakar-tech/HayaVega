"""Opt-in delivery for Mangoo Pulse events."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from threading import RLock, Thread

from .contracts import PulseEvent, PulseSubscriber


_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
LOGGER = logging.getLogger("mangoo.pulse")


@dataclass(frozen=True)
class PulseDeliveryFailure:
    """A subscriber error captured without interrupting other handlers."""

    event: PulseEvent
    subscriber: PulseSubscriber
    error: Exception


class PulseBus:
    """An explicit, in-process event registry with deterministic delivery.

    Subscribers run in connection order. Exceptions are recorded and logged,
    then later subscribers continue to receive the event.

    Delivery semantics: ``emit``/``publish`` run subscribers synchronously on
    the caller's thread and return each failure, so call them only after any
    surrounding database transaction has committed -- a subscriber must never
    observe a change that could still be rolled back. ``emit_async``/
    ``publish_async`` hand the same ordered subscriber list to a background
    thread and return immediately without failures; use them only where the
    caller does not need delivery confirmation, such as per-request lifecycle
    events, since a slow or failing subscriber can no longer affect the
    caller's control flow.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[PulseSubscriber]] = {}
        self._lock = RLock()

    def connect(self, event_name: str, subscriber: PulseSubscriber) -> None:
        self._validate_event_name(event_name)
        if not callable(subscriber):
            raise TypeError("Pulse subscribers must be callable.")
        with self._lock:
            subscribers = self._subscribers.setdefault(event_name, [])
            if subscriber not in subscribers:
                subscribers.append(subscriber)

    subscribe = connect

    def disconnect(self, event_name: str, subscriber: PulseSubscriber) -> bool:
        self._validate_event_name(event_name)
        with self._lock:
            subscribers = self._subscribers.get(event_name, [])
            if subscriber not in subscribers:
                return False
            subscribers.remove(subscriber)
            if not subscribers:
                del self._subscribers[event_name]
            return True

    unsubscribe = disconnect

    def emit(self, event: PulseEvent) -> tuple[PulseDeliveryFailure, ...]:
        if not isinstance(event, PulseEvent):
            raise TypeError("Pulse can emit only PulseEvent instances.")
        with self._lock:
            subscribers = tuple(self._subscribers.get(event.name, ()))
        failures = []
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception as error:
                LOGGER.exception("Pulse subscriber failed for event=%s", event.name)
                failures.append(PulseDeliveryFailure(event, subscriber, error))
        return tuple(failures)

    def publish(self, name: str, *, actor_id: int | None = None, data: dict | None = None) -> tuple[PulseDeliveryFailure, ...]:
        return self.emit(PulseEvent(name, actor_id=actor_id, data=data or {}))

    def emit_async(self, event: PulseEvent) -> None:
        """Hand delivery to a daemon background thread; failures are logged, not returned."""
        if not isinstance(event, PulseEvent):
            raise TypeError("Pulse can emit only PulseEvent instances.")
        with self._lock:
            subscribers = tuple(self._subscribers.get(event.name, ()))
        if subscribers:
            Thread(target=self._deliver, args=(event, subscribers), name=f"pulse-{event.name}", daemon=True).start()

    def publish_async(self, name: str, *, actor_id: int | None = None, data: dict | None = None) -> None:
        self.emit_async(PulseEvent(name, actor_id=actor_id, data=data or {}))

    def _deliver(self, event: PulseEvent, subscribers: tuple[PulseSubscriber, ...]) -> None:
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                LOGGER.exception("Pulse subscriber failed for event=%s", event.name)

    @staticmethod
    def _validate_event_name(event_name: str) -> None:
        if not isinstance(event_name, str) or not _EVENT_NAME.fullmatch(event_name):
            raise ValueError("Pulse event names must use dot-separated lowercase identifiers.")
