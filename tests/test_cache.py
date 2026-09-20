import sys
import time
import unittest
from unittest.mock import patch

from mangoo.cache import CacheBackendError, InMemoryCache, RedisCache, cached_view
from mangoo.response import Response, json_response


class InMemoryCacheTests(unittest.TestCase):
    def test_get_returns_default_for_missing_key(self):
        cache = InMemoryCache()
        self.assertIsNone(cache.get("missing"))
        self.assertEqual(cache.get("missing", "fallback"), "fallback")

    def test_set_then_get_round_trips_any_python_value(self):
        cache = InMemoryCache()
        cache.set("key", {"a": [1, 2, object()]})
        value = cache.get("key")
        self.assertEqual(value["a"][:2], [1, 2])

    def test_delete_removes_the_entry(self):
        cache = InMemoryCache()
        cache.set("key", "value")
        self.assertTrue(cache.delete("key"))
        self.assertIsNone(cache.get("key"))

    def test_delete_missing_key_returns_false(self):
        cache = InMemoryCache()
        self.assertFalse(cache.delete("missing"))

    def test_clear_removes_everything(self):
        cache = InMemoryCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        self.assertIsNone(cache.get("a"))
        self.assertIsNone(cache.get("b"))

    def test_ttl_expires_the_entry(self):
        cache = InMemoryCache()
        now = time.monotonic()
        with patch("mangoo.cache.time.monotonic", side_effect=[now, now, now + 10]):
            cache.set("key", "value", ttl=1)
            self.assertEqual(cache.get("key"), "value")
            self.assertIsNone(cache.get("key"))

    def test_default_ttl_applies_when_not_overridden(self):
        cache = InMemoryCache(default_ttl=1)
        now = time.monotonic()
        with patch("mangoo.cache.time.monotonic", side_effect=[now, now, now + 10]):
            cache.set("key", "value")
            self.assertEqual(cache.get("key"), "value")
            self.assertIsNone(cache.get("key"))

    def test_per_call_ttl_overrides_default_ttl(self):
        cache = InMemoryCache(default_ttl=1)
        cache.set("key", "value", ttl=None)
        self.assertEqual(cache.get("key"), "value")

    def test_max_entries_evicts_least_recently_used(self):
        cache = InMemoryCache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")  # touch a, so b becomes the least-recently-used entry
        cache.set("c", 3)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), 1)
        self.assertEqual(cache.get("c"), 3)

    def test_max_entries_must_be_positive(self):
        with self.assertRaises(ValueError):
            InMemoryCache(max_entries=0)

    def test_get_or_set_computes_once_on_miss(self):
        cache = InMemoryCache()
        calls = []

        def factory():
            calls.append(1)
            return "computed"

        self.assertEqual(cache.get_or_set("key", factory), "computed")
        self.assertEqual(cache.get_or_set("key", factory), "computed")
        self.assertEqual(len(calls), 1)

    def test_get_or_set_does_not_cache_falsy_values_as_missing(self):
        cache = InMemoryCache()
        calls = []

        def factory():
            calls.append(1)
            return 0

        self.assertEqual(cache.get_or_set("key", factory), 0)
        self.assertEqual(cache.get_or_set("key", factory), 0)
        self.assertEqual(len(calls), 1)

    def test_stats_reports_tracked_keys_and_capacity(self):
        cache = InMemoryCache(max_entries=5)
        cache.set("a", 1)
        self.assertEqual(cache.stats(), {"tracked_keys": 1, "max_entries": 5})


class RedisCacheMissingDependencyTests(unittest.TestCase):
    def test_missing_redis_package_raises_backend_error(self):
        with patch.dict(sys.modules, {"redis": None}):
            with self.assertRaises(CacheBackendError):
                RedisCache()


class CachedViewTests(unittest.TestCase):
    def _request(self, method="GET", path="/items", raw_query=""):
        from mangoo.request import Request

        return Request(method=method, path=path, query_params={}, body=b"", headers={}, raw_query=raw_query)

    def test_second_get_is_served_from_cache_without_calling_the_view(self):
        cache = InMemoryCache()
        calls = []

        @cached_view(cache)
        def view(request):
            calls.append(1)
            return Response("hello")

        request = self._request()
        first = view(request)
        second = view(request)
        self.assertEqual(first.body, "hello")
        self.assertEqual(second.body, "hello")
        self.assertEqual(len(calls), 1)

    def test_different_query_strings_are_cached_separately(self):
        cache = InMemoryCache()

        @cached_view(cache)
        def view(request):
            return Response(f"body for {request.raw_query}")

        first = view(self._request(raw_query="a=1"))
        second = view(self._request(raw_query="a=2"))
        self.assertEqual(first.body, "body for a=1")
        self.assertEqual(second.body, "body for a=2")

    def test_non_get_requests_are_never_cached(self):
        cache = InMemoryCache()
        calls = []

        @cached_view(cache)
        def view(request):
            calls.append(1)
            return Response("posted")

        request = self._request(method="POST")
        view(request)
        view(request)
        self.assertEqual(len(calls), 2)

    def test_bytes_response_body_is_normalized_to_str_and_restored(self):
        cache = InMemoryCache()

        @cached_view(cache)
        def view(request):
            return json_response({"ok": True})

        request = self._request()
        first = view(request)
        second = view(request)
        self.assertEqual(first.body, b'{"ok":true}')
        self.assertEqual(second.body, '{"ok":true}')
        self.assertEqual(second.content_type, "application/json; charset=utf-8")

    def test_custom_key_func_is_honored(self):
        cache = InMemoryCache()
        calls = []

        @cached_view(cache, key_func=lambda request: "fixed-key")
        def view(request):
            calls.append(1)
            return Response(f"body {len(calls)}")

        view(self._request(raw_query="a=1"))
        view(self._request(raw_query="a=2"))  # different query, same cache key
        self.assertEqual(len(calls), 1)

    def test_status_and_headers_survive_a_cache_hit(self):
        cache = InMemoryCache()

        @cached_view(cache)
        def view(request):
            return Response("nope", status=404, headers=(("X-Custom", "yes"),))

        first = view(self._request())
        second = view(self._request())
        self.assertEqual(second.status, 404)
        self.assertIn(("X-Custom", "yes"), second.headers)
        self.assertEqual(first.status, second.status)

    def test_async_view_is_cached_across_calls(self):
        import asyncio

        cache = InMemoryCache()
        calls = []

        @cached_view(cache)
        async def view(request):
            calls.append(1)
            return Response("async body")

        async def run():
            first = await view(self._request())
            second = await view(self._request())
            return first, second

        first, second = asyncio.run(run())
        self.assertEqual(first.body, "async body")
        self.assertEqual(second.body, "async body")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
