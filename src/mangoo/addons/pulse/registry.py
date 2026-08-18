"""Always-async delivery for Mangoo Pulse events, Rust-backed when available."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import re
from threading import RLock

from .contracts import PulseEvent, PulseSubscriber

try:
    from mangoo_native import PulseDispatcher as _NativeDispatcher
except ImportError:
    _NativeDispatcher = None


_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
LOGGER = logging.getLogger("mangoo.pulse")


class _FallbackDispatcher:
    """Persistent Python thread pool used when ``mangoo_native`` is not built.

    Behavior-compatible with the native dispatcher: a fixed pool of reused
    worker threads, not one ``threading.Thread`` per event.
    """

    def __init__(self, workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(workers, 1), thread_name_prefix="pulse")

    def dispatch(self, subscribers: list[PulseSubscriber], event: PulseEvent) -> None:
        self._executor.submit(self._deliver, subscribers, event)

    def close(self) -> None:
        self._executor.shutdown(wait=False)

    @staticmethod
    def _deliver(subscribers: list[PulseSubscriber], event: PulseEvent) -> None:
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                LOGGER.exception("Pulse subscriber failed for event=%s", event.name)


class PulseBus:
    """An explicit, in-process event registry with deterministic, always-async delivery.

    Subscribers run in connection order on a small persistent worker pool: a
    Rust-native thread pool (``mangoo_native.PulseDispatcher``) when the
    optional extension is built, otherwise a behavior-compatible Python
    ``ThreadPoolExecutor`` fallback. There is no synchronous alternative to
    opt into, and no thread-vs-process or sync-vs-async choice for callers to
    make -- ``publish``/``emit`` always hand delivery to a worker and return
    immediately.

    Call ``publish``/``emit`` only after any surrounding database transaction
    has committed, since a subscriber must never observe a change that could
    still be rolled back. Because delivery happens on another thread, a
    failing subscriber is logged rather than raised or returned to the
    caller; do not rely on ``publish``/``emit`` to observe delivery failures.
    """

    def __init__(self, *, workers: int = 4) -> None:
        self._subscribers: dict[str, list[PulseSubscriber]] = {}
        self._lock = RLock()
        self._dispatcher = _NativeDispatcher(workers) if _NativeDispatcher else _FallbackDispatcher(workers)

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

    def emit(self, event: PulseEvent) -> None:
        if not isinstance(event, PulseEvent):
            raise TypeError("Pulse can emit only PulseEvent instances.")
        with self._lock:
            subscribers = list(self._subscribers.get(event.name, ()))
        if subscribers:
            self._dispatcher.dispatch(subscribers, event)

    def publish(self, name: str, *, actor_id: int | None = None, data: dict | None = None) -> None:
        self.emit(PulseEvent(name, actor_id=actor_id, data=data or {}))

    def close(self) -> None:
        """Stop the worker pool; idle workers exit once queued jobs drain."""
        self._dispatcher.close()

    @staticmethod
    def _validate_event_name(event_name: str) -> None:
        if not isinstance(event_name, str) or not _EVENT_NAME.fullmatch(event_name):
            raise ValueError("Pulse event names must use dot-separated lowercase identifiers.")
