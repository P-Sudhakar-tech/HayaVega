"""Verifies mango cacheserver two ways: direct protocol-level unit tests
(no network, no dependency), and a real `redis` package client talking to
a real running instance of the server -- the test that actually proves
wire compatibility, not just that this module's own encoder and decoder
agree with each other."""

import asyncio
import threading
import unittest

import redis

from mangoo import cacheserver
from mangoo.cache import RedisCache


class EncodingTests(unittest.TestCase):
    def test_simple_string(self):
        self.assertEqual(cacheserver._simple_string("OK"), b"+OK\r\n")

    def test_error(self):
        self.assertEqual(cacheserver._error("ERR broken"), b"-ERR broken\r\n")

    def test_integer(self):
        self.assertEqual(cacheserver._integer(42), b":42\r\n")

    def test_bulk_string(self):
        self.assertEqual(cacheserver._bulk_string(b"hello"), b"$5\r\nhello\r\n")

    def test_bulk_string_nil_resp2(self):
        self.assertEqual(cacheserver._bulk_string(None, protocol=2), b"$-1\r\n")

    def test_bulk_string_nil_resp3(self):
        self.assertEqual(cacheserver._bulk_string(None, protocol=3), b"_\r\n")

    def test_array(self):
        self.assertEqual(cacheserver._array([cacheserver._integer(1), cacheserver._integer(2)]), b"*2\r\n:1\r\n:2\r\n")

    def test_map(self):
        pairs = [(cacheserver._bulk_string(b"a"), cacheserver._integer(1))]
        self.assertEqual(cacheserver._map(pairs), b"%1\r\n$1\r\na\r\n:1\r\n")


class StoreTests(unittest.TestCase):
    def test_get_returns_none_for_missing_key(self):
        store = cacheserver._Store()
        self.assertIsNone(store.get("missing"))

    def test_set_then_get_round_trips(self):
        store = cacheserver._Store()
        store.set("key", b"value", None)
        self.assertEqual(store.get("key"), b"value")

    def test_ttl_expires_the_entry(self):
        import time
        from unittest.mock import patch

        store = cacheserver._Store()
        now = time.time()
        with patch("mangoo.cacheserver.time.time", side_effect=[now, now, now + 10]):
            store.set("key", b"value", 1)
            self.assertEqual(store.get("key"), b"value")
            self.assertIsNone(store.get("key"))

    def test_delete_counts_only_keys_that_existed(self):
        store = cacheserver._Store()
        store.set("a", b"1", None)
        self.assertEqual(store.delete(["a", "missing"]), 1)
        self.assertIsNone(store.get("a"))

    def test_exists_counts_live_keys(self):
        store = cacheserver._Store()
        store.set("a", b"1", None)
        self.assertEqual(store.exists(["a", "missing"]), 1)

    def test_expire_updates_ttl_of_an_existing_key(self):
        store = cacheserver._Store()
        store.set("a", b"1", None)
        self.assertTrue(store.expire("a", 10))
        self.assertFalse(store.expire("missing", 10))

    def test_scan_matches_a_glob_pattern(self):
        store = cacheserver._Store()
        store.set("prefix:a", b"1", None)
        store.set("prefix:b", b"2", None)
        store.set("other", b"3", None)
        self.assertEqual(sorted(store.scan("prefix:*")), ["prefix:a", "prefix:b"])

    def test_scan_with_no_pattern_returns_every_key(self):
        store = cacheserver._Store()
        store.set("a", b"1", None)
        store.set("b", b"2", None)
        self.assertEqual(sorted(store.scan(None)), ["a", "b"])

    def test_flush_removes_everything(self):
        store = cacheserver._Store()
        store.set("a", b"1", None)
        store.flush()
        self.assertIsNone(store.get("a"))


class HostSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_a_non_loopback_host_without_a_password(self):
        with self.assertRaisesRegex(ValueError, "password"):
            await cacheserver.start_server("0.0.0.0", 0)

    async def test_allows_a_non_loopback_host_with_a_password(self):
        server = await cacheserver.start_server("0.0.0.0", 0, password="secret")
        try:
            self.assertTrue(server.sockets)
        finally:
            server.close()
            await server.wait_closed()

    async def test_loopback_host_needs_no_password(self):
        server = await cacheserver.start_server("127.0.0.1", 0)
        try:
            self.assertTrue(server.sockets)
        finally:
            server.close()
            await server.wait_closed()


class _RunningServer:
    """Runs a real `cacheserver` instance in a background thread with its
    own event loop, so a synchronous (blocking) `redis`-client test can
    talk to it -- the service under test is this repo's own code, not an
    external one, so no `MANGOO_TEST_REDIS_URL`-style gating is needed."""

    def __init__(self, *, password: str | None = None, host: str = "127.0.0.1"):
        self.password = password
        self.host = host
        self.port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        ready = threading.Event()
        error: list[BaseException] = []

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                self._server = loop.run_until_complete(cacheserver.start_server(self.host, 0, password=self.password))
                self.port = self._server.sockets[0].getsockname()[1]
            except BaseException as exc:  # surfaced on the main thread via `error`
                error.append(exc)
            finally:
                ready.set()
            if self._server is not None:
                loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        if not ready.wait(timeout=5):
            raise RuntimeError("cacheserver did not start within 5 seconds")
        if error:
            raise error[0]

    def stop(self) -> None:
        assert self._loop is not None and self._server is not None and self._thread is not None
        loop, server, thread = self._loop, self._server, self._thread

        async def _close() -> None:
            server.close()
            await server.wait_closed()

        asyncio.run_coroutine_threadsafe(_close(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)

    def url(self, *, with_password: bool = False) -> str:
        auth = f":{self.password}@" if with_password and self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/0"


class RealClientTests(unittest.TestCase):
    """The real `redis` package client, talking real RESP, against a real
    running `cacheserver` instance -- this is what actually proves wire
    compatibility, not just that this module agrees with itself."""

    @classmethod
    def setUpClass(cls):
        cls.server = _RunningServer()
        cls.server.start()
        cls.client = redis.Redis.from_url(cls.server.url(), socket_timeout=5)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.stop()

    def setUp(self):
        self.client.flushdb()

    def test_ping(self):
        self.assertTrue(self.client.ping())

    def test_set_then_get_round_trips(self):
        self.client.set("key", b"hello")
        self.assertEqual(self.client.get("key"), b"hello")

    def test_get_missing_key_returns_none(self):
        self.assertIsNone(self.client.get("missing"))

    def test_set_with_ex_expires(self):
        import time

        self.client.set("key", b"value", ex=1)
        self.assertEqual(self.client.get("key"), b"value")
        time.sleep(1.2)
        self.assertIsNone(self.client.get("key"))

    def test_delete(self):
        self.client.set("key", b"value")
        self.assertEqual(self.client.delete("key"), 1)
        self.assertIsNone(self.client.get("key"))
        self.assertEqual(self.client.delete("key"), 0)

    def test_exists(self):
        self.client.set("key", b"value")
        self.assertEqual(self.client.exists("key", "missing"), 1)

    def test_expire(self):
        self.client.set("key", b"value")
        self.assertTrue(self.client.expire("key", 10))
        self.assertFalse(self.client.expire("missing", 10))

    def test_scan_with_match(self):
        self.client.set("prefix:a", b"1")
        self.client.set("prefix:b", b"2")
        self.client.set("other", b"3")
        cursor, keys = self.client.scan(0, match="prefix:*", count=100)
        self.assertEqual(cursor, 0)
        self.assertEqual(sorted(keys), [b"prefix:a", b"prefix:b"])

    def test_unknown_command_returns_a_clean_error_not_a_hang(self):
        with self.assertRaises(redis.exceptions.ResponseError):
            self.client.execute_command("NOTACOMMAND")

    def test_eval_returns_a_clear_unsupported_error(self):
        with self.assertRaisesRegex(redis.exceptions.ResponseError, "unsupported"):
            self.client.eval("return 1", 0)


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _RunningServer(password="hunter2")
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_commands_without_a_password_are_rejected(self):
        client = redis.Redis.from_url(self.server.url(with_password=False), socket_timeout=5)
        with self.assertRaises(redis.exceptions.AuthenticationError):
            client.get("x")

    def test_wrong_password_is_rejected(self):
        client = redis.Redis.from_url(f"redis://:wrong@{self.server.host}:{self.server.port}/0", socket_timeout=5)
        with self.assertRaises(redis.exceptions.AuthenticationError):
            client.get("x")

    def test_correct_password_is_accepted(self):
        client = redis.Redis.from_url(self.server.url(with_password=True), socket_timeout=5)
        client.set("x", b"1")
        self.assertEqual(client.get("x"), b"1")


class RedisCacheOverCacheServerTests(unittest.TestCase):
    """`RedisCache` pointed at `mango cacheserver` instead of a real Redis
    instance -- the actual point of this feature: zero client-side code,
    the same `RedisCache` this project already ships and tests."""

    @classmethod
    def setUpClass(cls):
        cls.server = _RunningServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _cache(self, **kwargs):
        return RedisCache(self.server.url(), **kwargs)

    def test_set_then_get_round_trips_json_safe_values(self):
        cache = self._cache()
        cache.set("key", {"a": [1, 2, 3], "b": "text", "c": None})
        self.assertEqual(cache.get("key"), {"a": [1, 2, 3], "b": "text", "c": None})

    def test_get_returns_default_for_missing_key(self):
        cache = self._cache()
        self.assertEqual(cache.get("missing", "fallback"), "fallback")

    def test_ttl_expires_the_key(self):
        import time

        cache = self._cache()
        cache.set("key", "value", ttl=1)
        self.assertEqual(cache.get("key"), "value")
        time.sleep(1.2)
        self.assertIsNone(cache.get("key"))

    def test_delete_removes_the_key(self):
        cache = self._cache()
        cache.set("key", "value")
        self.assertTrue(cache.delete("key"))
        self.assertIsNone(cache.get("key"))

    def test_get_or_set_computes_once_on_miss(self):
        cache = self._cache()
        calls = []

        def factory():
            calls.append(1)
            return "computed"

        self.assertEqual(cache.get_or_set("key", factory), "computed")
        self.assertEqual(cache.get_or_set("key", factory), "computed")
        self.assertEqual(len(calls), 1)

    def test_clear_removes_only_this_caches_prefix(self):
        mine = self._cache(key_prefix="mine:")
        theirs = self._cache(key_prefix="theirs:")
        mine.set("key", "mine")
        theirs.set("key", "theirs")

        mine.clear()

        self.assertIsNone(mine.get("key"))
        self.assertEqual(theirs.get("key"), "theirs")

    def test_two_instances_with_the_same_prefix_share_state(self):
        first = self._cache()
        second = self._cache()
        first.set("key", "written by the first instance")
        self.assertEqual(second.get("key"), "written by the first instance")


class ProtocolRobustnessTests(unittest.IsolatedAsyncioTestCase):
    """Malformed input must close the connection with a clean protocol
    error, never hang or crash the server -- verified with a raw asyncio
    stream rather than the `redis` client, which would never send
    malformed input itself."""

    async def test_malformed_input_gets_a_protocol_error_and_the_connection_closes(self):
        server = await cacheserver.start_server("127.0.0.1", 0)
        try:
            host, port = server.sockets[0].getsockname()
            reader, writer = await asyncio.open_connection(host, port)
            writer.write(b"*not-a-number\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), timeout=5)
            self.assertTrue(response.startswith(b"-ERR"))
            writer.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_an_oversized_line_with_no_newline_gets_a_clean_protocol_error(self):
        """A line (inline command or RESP header) with no `\\r\\n` within
        `asyncio.StreamReader`'s own buffer limit raises
        `asyncio.LimitOverrunError` -- a real, plausible hostile-input
        case, not just `_ProtocolError`'s hand-rolled validation
        failures, and it needs the same clean response and connection
        close, not an unhandled-task-exception log line.
        """
        server = await cacheserver.start_server("127.0.0.1", 0)
        try:
            host, port = server.sockets[0].getsockname()
            reader, writer = await asyncio.open_connection(host, port)
            writer.write(b"A" * 100_000)  # no \r\n anywhere, exceeds the default 64KiB line buffer
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), timeout=5)
            self.assertTrue(response.startswith(b"-ERR"))
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    async def test_a_second_connection_still_works_after_a_malformed_first_one(self):
        """A blocking `redis` client can't be used here: this test, like
        the rest of `ProtocolRobustnessTests`, runs the server on the same
        thread/event loop as the test itself (`IsolatedAsyncioTestCase`),
        so a synchronous client call would block the one thread the
        server also needs to answer it -- a self-deadlock, not a server
        bug (see `RealClientTests` and friends, which correctly run the
        server on its own background thread specifically so a real
        blocking `redis` client can be used safely). A second raw async
        connection proves the same thing -- the server keeps serving
        other connections -- without that hazard.
        """
        server = await cacheserver.start_server("127.0.0.1", 0)
        try:
            host, port = server.sockets[0].getsockname()
            bad_reader, bad_writer = await asyncio.open_connection(host, port)
            bad_writer.write(b"garbage\r\n*999999999999\r\n")
            await bad_writer.drain()
            await asyncio.wait_for(bad_reader.readline(), timeout=5)  # "unknown command 'GARBAGE'"
            await asyncio.wait_for(bad_reader.readline(), timeout=5)  # the protocol error that closes the connection
            bad_writer.close()
            await bad_writer.wait_closed()

            good_reader, good_writer = await asyncio.open_connection(host, port)
            good_writer.write(b"*1\r\n$4\r\nPING\r\n")
            await good_writer.drain()
            response = await asyncio.wait_for(good_reader.readline(), timeout=5)
            self.assertEqual(response, b"+PONG\r\n")
            good_writer.close()
            await good_writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
