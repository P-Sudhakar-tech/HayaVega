"""A small, pluggable cache abstraction for Mangoo.

Two backends share one interface (``get``/``set``/``delete``/``clear``/
``get_or_set``): ``InMemoryCache``, a thread-safe, TTL-aware, bounded LRU
cache for a single process (no dependency, mirrors ``hardening.RateLimiter``'s
design), and ``RedisCache``, which shares cached values across every process
that points at the same Redis instance -- the same opt-in, already-a-
dependency-for-rate-limiting Redis story ``hardening.RedisRateLimiter`` uses.
Pick whichever matches your deployment; nothing else in this module assumes
one over the other.
"""

from __future__ import annotations

import inspect
import time
from collections import OrderedDict
from functools import wraps
from threading import Lock
from typing import TYPE_CHECKING, Any

import msgspec

from .response import Response

if TYPE_CHECKING:
    from .request import Request

_MISSING = object()


class CacheBackendError(RuntimeError):
    """Raised when a configured cache backend's dependency is unavailable."""


class InMemoryCache:
    """Thread-safe, in-memory cache for one process, with per-entry TTL and bounded size.

    Eviction mirrors ``hardening.RateLimiter``: a bounded ``OrderedDict`` used
    as an LRU -- the least-recently-touched key is dropped once ``max_entries``
    is reached, so a cache under heavy, varied key pressure can't grow without
    bound. Values are stored as-is (no serialization), so anything Python can
    hold is a valid value -- this backend has no need for ``RedisCache``'s
    JSON-safety constraint.
    """

    def __init__(self, max_entries: int = 10_000, default_ttl: float | None = None):
        if max_entries < 1:
            raise ValueError("max_entries must be a positive integer.")
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        self._entries: OrderedDict[str, tuple[float | None, Any]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str, default: Any = None) -> Any:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return default
            expires_at, value = entry
            if expires_at is not None and expires_at <= now:
                del self._entries[key]
                return default
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        ttl = self.default_ttl if ttl is None else ttl
        expires_at = None if ttl is None else time.monotonic() + ttl
        with self._lock:
            if key in self._entries:
                del self._entries[key]
            elif len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)
            self._entries[key] = (expires_at, value)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get_or_set(self, key: str, default_factory, ttl: float | None = None) -> Any:
        value = self.get(key, _MISSING)
        if value is not _MISSING:
            return value
        value = default_factory()
        self.set(key, value, ttl=ttl)
        return value

    def stats(self) -> dict[str, int]:
        """Return non-sensitive cache capacity information for monitoring."""
        with self._lock:
            return {"tracked_keys": len(self._entries), "max_entries": self.max_entries}


class RedisCache:
    """Redis-backed cache shared across every process pointed at the same Redis instance.

    Values are JSON-encoded via ``msgspec`` (Mangoo's existing core JSON
    dependency, already used the same way by ``mangoo.jobs`` for payloads) --
    so only JSON-safe values (``dict``/``list``/``str``/``int``/``float``/
    ``bool``/``None``, and anything ``msgspec`` can encode) round-trip
    correctly. Unlike ``InMemoryCache``, this has no local size bound of its
    own; Redis's own memory policy governs eviction if one is configured.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0", *, default_ttl: float | None = None, key_prefix: str = "mangoo:cache:"):
        try:
            import redis
        except ImportError as error:
            raise CacheBackendError("Redis-backed caching requires `uv add redis`.") from error
        self.redis_url = redis_url
        self.default_ttl = default_ttl
        self.key_prefix = key_prefix
        self._client = redis.Redis.from_url(redis_url)

    def _redis_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"

    def get(self, key: str, default: Any = None) -> Any:
        raw = self._client.get(self._redis_key(key))
        return default if raw is None else msgspec.json.decode(raw)

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        ttl = self.default_ttl if ttl is None else ttl
        encoded = msgspec.json.encode(value)
        if ttl is None:
            self._client.set(self._redis_key(key), encoded)
        else:
            self._client.set(self._redis_key(key), encoded, ex=int(ttl))

    def delete(self, key: str) -> bool:
        return bool(self._client.delete(self._redis_key(key)))

    def clear(self) -> None:
        """Delete every key this cache owns (matched by ``key_prefix``).

        Uses ``SCAN`` rather than ``KEYS``: ``KEYS`` blocks the entire Redis
        server until it has walked the whole keyspace, which is unsafe on a
        shared production instance; ``SCAN`` walks it incrementally instead.
        """
        cursor = 0
        pattern = f"{self.key_prefix}*"
        while True:
            cursor, keys = self._client.scan(cursor=cursor, match=pattern, count=500)
            if keys:
                self._client.delete(*keys)
            if cursor == 0:
                break

    def get_or_set(self, key: str, default_factory, ttl: float | None = None) -> Any:
        value = self.get(key, _MISSING)
        if value is not _MISSING:
            return value
        value = default_factory()
        self.set(key, value, ttl=ttl)
        return value


def _default_cache_key(request: Request) -> str:
    return f"{request.path}?{request.raw_query}"


def cached_view(cache, ttl: float | None = None, *, key_func=None):
    """Cache a view's rendered ``Response``, keyed by request path and query string by default.

    Only ``GET`` requests are served from, or written to, the cache -- caching
    a response to a request that's meant to have a side effect (``POST``,
    ``PUT``, ``DELETE``, ...) would risk serving a stale result in place of
    actually performing it. Nothing invalidates a cached entry except its own
    ``ttl`` expiring or an explicit ``cache.delete()``/``cache.clear()`` call,
    so pick ``ttl`` deliberately for data that changes.

    ``response.body`` is normalized to ``str`` before caching (``bytes``
    bodies, e.g. from ``json_response``, are UTF-8 decoded) so a cached entry
    is always JSON-safe and works identically against ``InMemoryCache`` and
    ``RedisCache``. ``view`` may be ``async def`` when deployed under
    ``mangoo.asgi``: the wrapper stays a coroutine function itself so the
    ASGI runtime awaits it directly; the cache backend calls themselves stay
    synchronous either way.
    """

    def decorator(view):
        def _from_cache(entry) -> Response:
            body, status, content_type, headers = entry
            return Response(body, status=status, content_type=content_type, headers=tuple(tuple(header) for header in headers))

        def _store(request, response: Response) -> None:
            key = key_func(request) if key_func else _default_cache_key(request)
            body = response.body.decode("utf-8") if isinstance(response.body, bytes) else response.body
            cache.set(key, (body, response.status, response.content_type, [list(header) for header in response.headers]), ttl=ttl)

        if inspect.iscoroutinefunction(view):

            @wraps(view)
            async def wrapped(request):
                if request.method != "GET":
                    return await view(request)
                key = key_func(request) if key_func else _default_cache_key(request)
                cached_entry = cache.get(key)
                if cached_entry is not None:
                    return _from_cache(cached_entry)
                response = await view(request)
                _store(request, response)
                return response

            return wrapped

        @wraps(view)
        def wrapped(request):
            if request.method != "GET":
                return view(request)
            key = key_func(request) if key_func else _default_cache_key(request)
            cached_entry = cache.get(key)
            if cached_entry is not None:
                return _from_cache(cached_entry)
            response = view(request)
            _store(request, response)
            return response

        return wrapped

    return decorator
