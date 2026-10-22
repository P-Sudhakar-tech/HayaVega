# Caching

A small, pluggable cache behind one interface -- `get`/`set`/`delete`/
`clear`/`get_or_set` -- with two backends, plus a decorator to cache a
whole view's response.

## Using a cache directly

```python
from mangoo.cache import InMemoryCache, RedisCache

cache = InMemoryCache(max_entries=10_000)  # one process, no dependency
cache = RedisCache("redis://localhost:6379/0")  # shared across every process pointed at it
```

`InMemoryCache` is a thread-safe, TTL-aware, bounded LRU cache -- the
same design as `hardening.RateLimiter`, just for arbitrary values
instead of request counts; it stores Python objects directly, no
serialization. `RedisCache` JSON-encodes values via `msgspec` (already
a core dependency), so only JSON-safe values round-trip; it shares
state across processes the same way `hardening.RedisRateLimiter` does,
and requires `uv add redis`.

## Configuring a backend from settings

```python title="config/settings.py"
class Settings:
    class cache:
        backend = "redis"  # "in_memory" (default) or "redis"
        redis_url = "redis://cache.internal:6379/0"
        default_ttl = 300
        key_prefix = "myapp:cache:"
```

```python
from mangoo.cache import cache_from_settings
from mangoo.conf import load_settings

settings = load_settings()
cache = cache_from_settings(settings.cache)
```

`"in_memory"` accepts `max_entries` (default `10_000`) and
`default_ttl`; `"redis"` accepts `redis_url` (default
`redis://localhost:6379/0`), `default_ttl`, and `key_prefix` (default
`mangoo:cache:`) -- the same `backend_from_settings()` shape
[Email](email.md) already uses.

## Caching a whole view

```python
from mangoo.cache import cached_view


@cached_view(cache, ttl=60)
def product_list(request): ...
```

Only `GET` requests are served from, or written to, the cache -- a
request meant to have a side effect (`POST`, `PUT`, `DELETE`, ...) is
never cached. The cache key defaults to the request's path and query
string; pass `key_func=` to key on something else instead:

```python
@cached_view(cache, ttl=60, key_func=lambda request: f"products:{request.user.id}")
def product_list(request): ...
```

`view` may be `async def` under [ASGI](asgi.md) -- the wrapper stays a
coroutine function itself so the ASGI runtime awaits it directly; the
cache backend calls stay synchronous either way.

Nothing invalidates a cached entry except its `ttl` expiring or an
explicit `cache.delete()`/`cache.clear()` call, so pick `ttl`
deliberately for data that changes.

## `get_or_set`

```python
featured = cache.get_or_set("featured_products", lambda: expensive_query(), ttl=300)
```

Computes `default_factory()` once on a miss, stores it, and returns it
-- repeated calls with the same key return the cached value until it
expires, without recomputing.
