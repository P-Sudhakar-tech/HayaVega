# Caching

A small, pluggable cache behind one interface -- `get`/`set`/`delete`/
`clear`/`get_or_set` -- with three backends, plus a decorator to cache
a whole view's response.

## Using a cache directly

```python
from mangoo.cache import InMemoryCache, RedisCache, SqliteCache

cache = InMemoryCache(max_entries=10_000)  # one process, no dependency
cache = SqliteCache("cache.sqlite3")  # every process on one machine, no dependency, no server
cache = RedisCache("redis://localhost:6379/0")  # shared across every process pointed at it, any number of machines
```

`InMemoryCache` is a thread-safe, TTL-aware, bounded LRU cache -- the
same design as `hardening.RateLimiter`, just for arbitrary values
instead of request counts; it stores Python objects directly, no
serialization.

`SqliteCache` shares values across every process pointed at the same
local SQLite file -- Redis's actual value (one shared cache instead of
one per worker) for several WSGI/ASGI worker processes on a single
machine, without running a separate service. It reuses `db.connect()`,
the exact connection layer `mangoo.jobs`/`mangoo.migrations` already
use, and enables WAL mode so a writer never blocks a reader and vice
versa. Like `RedisCache`, values are JSON-encoded via `msgspec`, so
only JSON-safe values round-trip, and a `key_prefix` scopes every key
(including `clear()`, which only clears its own prefix) so more than
one logical cache can safely share one file -- the same coexistence
`RedisCache` gives multiple caches sharing one Redis instance. The one
thing it can't do that Redis can: share state across *multiple
machines* -- SQLite has no network protocol, so a deployment that
spans more than one host still needs `RedisCache`.

`RedisCache` JSON-encodes values via `msgspec` (already a core
dependency); it shares state across processes the same way
`hardening.RedisRateLimiter` does, and requires `uv add redis`.

## Configuring a backend from settings

```python title="config/settings.py"
class Settings:
    class cache:
        backend = "sqlite"  # "in_memory" (default), "sqlite", or "redis"
        path = BASE_DIR / "cache.sqlite3"
        default_ttl = 300
```

```python
from mangoo.cache import cache_from_settings
from mangoo.conf import load_settings

settings = load_settings()
cache = cache_from_settings(settings.cache)
```

`"in_memory"` accepts `max_entries` (default `10_000`) and
`default_ttl`; `"sqlite"` accepts `path` (default
`mangoo_cache.sqlite3`, relative to the process's working directory --
pass an absolute path, e.g. `BASE_DIR / "cache.sqlite3"`, for anything
beyond local experimentation), `default_ttl`, and `key_prefix` (default
`mangoo:cache:`); `"redis"` accepts `redis_url` (default
`redis://localhost:6379/0`), `default_ttl`, and `key_prefix` (default
`mangoo:cache:`) -- the same
`backend_from_settings()` shape [Email](email.md) already uses.

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

## Running the built-in cache server

`RedisCache` talks to a server over the standard `redis` Python
package, which speaks real RESP on the wire -- so instead of installing
and running actual Redis, a project can run Mangoo's own built-in
cache server and point `RedisCache` at that instead, with **no client
code changes at all**:

```bash
mango cacheserver
```

```python
from mangoo.cache import RedisCache

cache = RedisCache()  # redis://localhost:6379/0 -- cacheserver's own default port
```

This is a real, standalone, networked TCP server (`mango cacheserver
[--host H] [--port P] [--password PW]`, default `127.0.0.1:6379`,
matching `RedisCache()`'s own zero-argument default) -- unlike
`SqliteCache`, it can be reached from **any number of machines**, the
same way a real Redis instance can: point several app servers at one
`mango cacheserver` process and they share one cache.

It implements enough of RESP2/RESP3 for `RedisCache` -- `GET`, `SET`
(`EX`/`PX`), `DEL`, `EXISTS`, `EXPIRE`, `SCAN` (`MATCH`/`COUNT`),
`FLUSHDB`/`FLUSHALL`, plus the `HELLO`/`AUTH` handshake modern
`redis-py` always performs -- and has been verified against the real
`redis` package, not just its own encoder/decoder. A real `redis-cli`
or anyone else's `redis-py` code also works against it for these same
commands.

**What it deliberately doesn't do**, stated plainly rather than
silently missing:

- No persistence (RDB/AOF) -- in-memory only, the same limitation
  `InMemoryCache` already has; data is lost on restart.
- No `EVAL`/Lua scripting -- `hardening.RedisRateLimiter` needs this
  for its atomic increment-and-expire, so it still needs a real Redis
  instance; `mango cacheserver` returns a clear error for `EVAL`
  rather than hanging.
- No multiple numbered databases (`SELECT n` is accepted as a no-op)
  -- use `key_prefix` for isolation, same as `RedisCache`/`SqliteCache`.
- No pub/sub, transactions, clustering, or ACLs beyond one password.

**Security**: `serve()` refuses to bind to anything other than
`127.0.0.1`/`localhost` unless `--password` is also given -- an
unauthenticated cache listening on a public interface is a real
vulnerability, not a theoretical one, so this is enforced rather than
just documented. For more than one machine, bind to a real interface
and set a password:

```bash
mango cacheserver --host 0.0.0.0 --port 6379 --password "$CACHE_PASSWORD"
```

```python
cache = RedisCache(f"redis://:{password}@cache-host:6379/0")
```
