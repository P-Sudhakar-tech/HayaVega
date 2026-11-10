"""A built-in, networked, in-memory cache server -- a real substitute for
installing Redis, not a new client.

`hayavega.cache.RedisCache` already talks to a cache server over the
standard `redis` Python package (`redis.Redis.from_url(...)`), which
speaks real RESP on the wire. This module implements just enough of
RESP2 -- and just the commands `RedisCache` actually issues, plus the
basics a real `redis-cli`/`redis-py` client needs to connect at all --
for `RedisCache` to work against it completely unchanged. Run it with
`hayavega cacheserver`, then point `RedisCache()`'s own zero-argument
default (`redis://localhost:6379/0`) at it -- no new client code, no
separate service to install.

Deliberately out of scope, stated plainly rather than silently missing:

- `EVAL`/Lua scripting (`hardening.RedisRateLimiter` needs this for its
  atomic INCR+EXPIRE) -- returns a clear error instead of hanging or
  crashing the connection. A real Redis instance remains required for
  `RedisRateLimiter`.
- Persistence (RDB/AOF) -- in-memory only, the same limitation
  `cache.InMemoryCache` already has; data is lost on restart.
- Multiple numbered databases (`SELECT n`) -- accepted as a no-op;
  use `key_prefix` for isolation, same as `RedisCache`/`SqliteCache`.
- Pub/sub, transactions, clustering, ACLs beyond a single password.
- Rust acceleration -- ships pure-`asyncio` first, matching
  `rust_runtime.py`'s own "prove it's needed before building it."
"""

from __future__ import annotations

import asyncio
import fnmatch
import time

_MAX_ARGS = 1024
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class _ProtocolError(Exception):
    pass


def _simple_string(value: str) -> bytes:
    return f"+{value}\r\n".encode()


def _error(message: str) -> bytes:
    return f"-{message}\r\n".encode()


def _integer(value: int) -> bytes:
    return f":{value}\r\n".encode()


def _bulk_string(value: bytes | None, *, protocol: int = 2) -> bytes:
    """A nil is the one place RESP2 and RESP3 actually disagree: RESP2
    has no dedicated null type and spells it `$-1\\r\\n` (a bulk string
    of length -1); RESP3 added a real null type, `_\\r\\n`, and modern
    `redis-py`'s RESP3 parser does not understand the old `$-1` form at
    all (it calls `int(-1)` as a read length and hangs). Every other
    reply here is identical under both protocols -- this is the one spot
    that must track which protocol the connection negotiated via
    `HELLO`.
    """
    if value is None:
        return b"_\r\n" if protocol == 3 else b"$-1\r\n"
    return b"$" + str(len(value)).encode() + b"\r\n" + value + b"\r\n"


def _array(items: list[bytes]) -> bytes:
    header = b"*" + str(len(items)).encode() + b"\r\n"
    return header + b"".join(items)


def _map(pairs: list[tuple[bytes, bytes]]) -> bytes:
    """A RESP3 map reply (`%<pair-count>\\r\\n` + flattened, already
    RESP-encoded key/value elements) -- used only for `HELLO`'s reply.
    Modern `redis-py` (8.x) defaults to requesting RESP3 and parses
    `HELLO`'s reply as a real map, not an array. RESP3 is a near-superset
    of RESP2 -- every other command here keeps replying with ordinary
    RESP2 types (simple string/error/integer/bulk string/array), which
    parse identically under both -- except nil, which RESP3 spells
    differently (see `_bulk_string`'s `protocol` parameter).
    """
    header = b"%" + str(len(pairs)).encode() + b"\r\n"
    return header + b"".join(key + value for key, value in pairs)


class _Store:
    """The server's actual cache state -- a dict of raw bytes, not Python
    objects: RESP values are bytes on the wire, and `RedisCache` already
    JSON-encodes before `SET`/decodes after `GET`, so there is nothing
    here to share with `cache.InMemoryCache`'s typed-object storage.
    Lazy expiry on read, the same pattern `InMemoryCache`/`SqliteCache`
    already use. Accessed from a single `asyncio` event loop -- one
    coroutine per connection, no real parallelism within the loop, so no
    lock is needed the way `InMemoryCache.Lock` needs one against a real
    OS thread pool.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[bytes, float | None]] = {}

    def get(self, key: str) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= time.time():
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value: bytes, ttl: float | None) -> None:
        expires_at = None if ttl is None else time.time() + ttl
        self._entries[key] = (value, expires_at)

    def delete(self, keys: list[str]) -> int:
        removed = 0
        for key in keys:
            if self.get(key) is not None:
                del self._entries[key]
                removed += 1
        return removed

    def exists(self, keys: list[str]) -> int:
        return sum(1 for key in keys if self.get(key) is not None)

    def expire(self, key: str, seconds: float) -> bool:
        if self.get(key) is None:
            return False
        value, _ = self._entries[key]
        self._entries[key] = (value, time.time() + seconds)
        return True

    def scan(self, pattern: str | None) -> list[str]:
        now = time.time()
        live = [key for key, (_, expires_at) in list(self._entries.items()) if expires_at is None or expires_at > now]
        return fnmatch.filter(live, pattern) if pattern is not None else live

    def flush(self) -> None:
        self._entries.clear()


async def _read_command(reader: asyncio.StreamReader, max_value_size: int) -> list[bytes] | None:
    """Read one client command: a real RESP multi-bulk array (what
    `redis-py` always sends), or a plain whitespace-separated inline
    command (what `redis-cli`/`nc` send interactively) -- `None` on a
    clean disconnect.
    """
    line = await reader.readline()
    if not line:
        return None
    if not line.endswith(b"\n"):
        raise _ProtocolError("incomplete line")
    line = line.rstrip(b"\r\n")
    if not line:
        return []
    if not line.startswith(b"*"):
        return line.split()
    try:
        count = int(line[1:])
    except ValueError:
        raise _ProtocolError("invalid multibulk length") from None
    if count < 0 or count > _MAX_ARGS:
        raise _ProtocolError("invalid multibulk length")
    args: list[bytes] = []
    for _ in range(count):
        header = await reader.readline()
        if not header.endswith(b"\n"):
            raise _ProtocolError("incomplete bulk header")
        header = header.rstrip(b"\r\n")
        if not header.startswith(b"$"):
            raise _ProtocolError("expected bulk string")
        try:
            length = int(header[1:])
        except ValueError:
            raise _ProtocolError("invalid bulk length") from None
        if length == -1:
            args.append(b"")
            continue
        if length < 0 or length > max_value_size:
            raise _ProtocolError("bulk value too large")
        data = await reader.readexactly(length)
        await reader.readexactly(2)  # trailing \r\n
        args.append(data)
    return args


def _dispatch(store: _Store, name: str, args: list[bytes], protocol: int) -> bytes:
    if name == "PING":
        return _simple_string("PONG")
    if name == "SELECT":
        return _simple_string("OK")
    if name == "GET":
        if len(args) != 1:
            return _error("ERR wrong number of arguments for 'get' command")
        return _bulk_string(store.get(args[0].decode("utf-8", "replace")), protocol=protocol)
    if name == "SET":
        if len(args) < 2:
            return _error("ERR wrong number of arguments for 'set' command")
        key = args[0].decode("utf-8", "replace")
        value = args[1]
        ttl: float | None = None
        index = 2
        while index < len(args):
            option = args[index].decode("utf-8", "replace").upper()
            if option == "EX" and index + 1 < len(args):
                ttl = float(args[index + 1])
                index += 2
            elif option == "PX" and index + 1 < len(args):
                ttl = float(args[index + 1]) / 1000
                index += 2
            else:
                index += 1
        store.set(key, value, ttl)
        return _simple_string("OK")
    if name == "DEL":
        if not args:
            return _error("ERR wrong number of arguments for 'del' command")
        return _integer(store.delete([key.decode("utf-8", "replace") for key in args]))
    if name == "EXISTS":
        if not args:
            return _error("ERR wrong number of arguments for 'exists' command")
        return _integer(store.exists([key.decode("utf-8", "replace") for key in args]))
    if name == "EXPIRE":
        if len(args) != 2:
            return _error("ERR wrong number of arguments for 'expire' command")
        ok = store.expire(args[0].decode("utf-8", "replace"), float(args[1]))
        return _integer(1 if ok else 0)
    if name == "SCAN":
        if not args:
            return _error("ERR wrong number of arguments for 'scan' command")
        pattern = None
        index = 1
        while index < len(args):
            option = args[index].decode("utf-8", "replace").upper()
            if option == "MATCH" and index + 1 < len(args):
                pattern = args[index + 1].decode("utf-8", "replace")
                index += 2
            elif option == "COUNT" and index + 1 < len(args):
                index += 2  # a hint only -- this store always returns everything in one pass
            else:
                index += 1
        keys = store.scan(pattern)
        return _array([_bulk_string(b"0"), _array([_bulk_string(key.encode("utf-8")) for key in keys])])
    if name in {"FLUSHDB", "FLUSHALL"}:
        store.flush()
        return _simple_string("OK")
    if name == "EVAL":
        return _error("ERR unsupported: hayavega cacheserver does not implement EVAL/Lua scripting -- use a real Redis instance for hardening.RedisRateLimiter.")
    return _error(f"ERR unknown command '{name}'")


def _hello_reply(requested_proto: int) -> bytes:
    return _map(
        [
            (_bulk_string(b"server"), _bulk_string(b"hayavega-cacheserver")),
            (_bulk_string(b"version"), _bulk_string(b"1.0.0")),
            (_bulk_string(b"proto"), _integer(requested_proto)),
            (_bulk_string(b"id"), _integer(1)),
            (_bulk_string(b"mode"), _bulk_string(b"standalone")),
            (_bulk_string(b"role"), _bulk_string(b"master")),
            (_bulk_string(b"modules"), _array([])),
        ]
    )


def _handle_hello(args: list[bytes], password: str | None, already_authenticated: bool, current_protocol: int) -> tuple[bytes, bool, int]:
    """`HELLO [protover] [AUTH username password]` -- modern `redis-py`
    (8.x, RESP3 by default) always sends this on connect, combining
    protocol negotiation and authentication into one call rather than a
    separate `AUTH`. Agreeing to `proto: 3` costs nothing here: every
    reply stays the same RESP2-compatible encoding it already was,
    except nils (see `_bulk_string`), which is exactly why the
    negotiated protocol has to be tracked and threaded back to the
    caller. Returns `(response, authenticated, protocol)` -- on a
    rejected negotiation (bad version, bad password), `protocol` is
    `current_protocol` unchanged.
    """
    requested_proto = 2
    index = 0
    if index < len(args):
        try:
            requested_proto = int(args[index])
            index += 1
        except ValueError:
            pass
    if requested_proto not in (2, 3):
        return _error("NOPROTO unsupported protocol version"), already_authenticated, current_protocol

    authenticated = already_authenticated
    while index < len(args):
        option = args[index].decode("utf-8", "replace").upper()
        if option == "AUTH" and index + 2 < len(args):
            supplied_password = args[index + 2]
            authenticated = password is not None and supplied_password == password.encode("utf-8")
            if not authenticated:
                return _error("WRONGPASS invalid username-password pair or user is disabled."), False, current_protocol
            index += 3
        else:
            index += 1

    if password is not None and not authenticated:
        return (
            _error("NOAUTH HELLO must be called with the client already authenticated, otherwise the HELLO <proto> AUTH <user> <pass> option can be used to authenticate the client and select the RESP protocol version at the same time"),
            authenticated,
            current_protocol,
        )

    return _hello_reply(requested_proto), authenticated, requested_proto


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, store: _Store, password: str | None, max_value_size: int) -> None:
    authenticated = password is None
    protocol = 2  # RESP2 until a client negotiates RESP3 via HELLO, same default real Redis uses
    try:
        while True:
            try:
                command = await _read_command(reader, max_value_size)
            except (_ProtocolError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
                # LimitOverrunError: a line (inline command or RESP header)
                # with no \r\n inside asyncio.StreamReader's own buffer
                # limit -- an oversized-but-well-formed-looking line is a
                # real, plausible malformed/hostile input, not just a
                # theoretical one, so it gets the same clean protocol
                # error and connection close as every other malformed
                # case here, instead of an unhandled-task-exception log.
                writer.write(_error("ERR Protocol error"))
                await writer.drain()
                break
            if command is None:
                break
            if not command:
                continue
            name = command[0].decode("utf-8", "replace").upper()
            args = command[1:]

            if name == "AUTH":
                if password is None:
                    writer.write(_error("ERR Client sent AUTH, but no password is set."))
                elif len(args) == 1 and args[0] == password.encode("utf-8"):
                    authenticated = True
                    writer.write(_simple_string("OK"))
                else:
                    writer.write(_error("WRONGPASS invalid username-password pair or user is disabled."))
                await writer.drain()
                continue

            if name == "HELLO":
                response, authenticated, protocol = _handle_hello(args, password, authenticated, protocol)
                writer.write(response)
                await writer.drain()
                continue

            if not authenticated:
                writer.write(_error("NOAUTH Authentication required."))
                await writer.drain()
                continue

            writer.write(_dispatch(store, name, args, protocol))
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def start_server(host: str = "127.0.0.1", port: int = 6379, *, password: str | None = None, max_value_size: int = 1_048_576) -> asyncio.Server:
    """Start the cache server and return the bound `asyncio.Server` (its
    `.sockets` expose the actual port -- useful when `port=0` asks the OS
    to pick a free one, e.g. in tests). Caller owns its lifecycle:
    `async with server: await server.serve_forever()`, or `server.close()`
    / `await server.wait_closed()` to stop it.

    Refuses to bind to anything other than `127.0.0.1`/`localhost`/`::1`
    unless `password` is also set -- an unauthenticated cache listening
    on a public interface is a real vulnerability class, not a
    theoretical one, so this is a hard rule rather than a documentation
    note (the same way `production.validate_production` refuses unsafe
    settings outright rather than just warning about them).
    """
    if host not in _LOOPBACK_HOSTS and password is None:
        raise ValueError(f"cacheserver.start_server(host={host!r}) also needs password= -- binding an unauthenticated cache to a non-loopback host is a real vulnerability. Pass password= to allow it, or keep host as '127.0.0.1'.")
    store = _Store()
    return await asyncio.start_server(lambda reader, writer: _handle_connection(reader, writer, store, password, max_value_size), host, port)


def serve(host: str = "127.0.0.1", port: int = 6379, *, password: str | None = None, max_value_size: int = 1_048_576) -> None:
    """Run the cache server until interrupted -- `hayavega cacheserver`'s
    implementation. `port` defaults to 6379 on purpose: it's the exact
    port `cache.RedisCache()`'s own zero-argument default
    (`redis://localhost:6379/0`) already points at, so the two just work
    together with nothing to configure.
    """
    asyncio.run(_run_forever(host, port, password=password, max_value_size=max_value_size))


async def _run_forever(host: str, port: int, *, password: str | None, max_value_size: int) -> None:
    server = await start_server(host, port, password=password, max_value_size=max_value_size)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or ())
    print(f"Hayavega cache server listening on {addresses}")
    async with server:
        await server.serve_forever()
