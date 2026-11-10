"""Opt-in integration coverage; set HAYAVEGA_TEST_REDIS_URL to run it.

Mirrors test_redis_ratelimiter_integration.py's pattern: the missing-
dependency check runs everywhere (it only needs `import redis` to fail),
while everything else requires a real Redis instance and is skipped unless
HAYAVEGA_TEST_REDIS_URL is set.
"""

import os
import sys
import time
import unittest
import uuid
from unittest.mock import patch

from hayavega.cache import CacheBackendError, RedisCache, cache_from_settings


class RedisCacheMissingDependencyTests(unittest.TestCase):
    def test_missing_redis_package_raises_backend_error(self):
        with patch.dict(sys.modules, {"redis": None}):
            with self.assertRaises(CacheBackendError):
                RedisCache()


@unittest.skipUnless(os.getenv("HAYAVEGA_TEST_REDIS_URL"), "Redis integration environment is not configured")
class RedisCacheIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.redis_url = os.environ["HAYAVEGA_TEST_REDIS_URL"]
        self.prefix = f"hayavega:cache:test:{uuid.uuid4()}:"
        try:
            self.cache = RedisCache(redis_url=self.redis_url, key_prefix=self.prefix)
            self.cache._client.ping()
        except CacheBackendError as error:
            self.skipTest(str(error))
        except Exception as error:  # noqa: BLE001 - any connection failure means "skip", not "fail"
            self.skipTest(f"Redis at {self.redis_url} is not reachable: {error}")
        self.addCleanup(self.cache.clear)

    def test_set_then_get_round_trips_json_safe_values(self):
        self.cache.set("key", {"a": [1, 2, 3], "b": "text", "c": None})
        self.assertEqual(self.cache.get("key"), {"a": [1, 2, 3], "b": "text", "c": None})

    def test_get_returns_default_for_missing_key(self):
        self.assertEqual(self.cache.get("missing", "fallback"), "fallback")

    def test_ttl_expires_the_key(self):
        self.cache.set("key", "value", ttl=1)
        self.assertEqual(self.cache.get("key"), "value")
        time.sleep(1.5)
        self.assertIsNone(self.cache.get("key"))

    def test_delete_removes_the_key(self):
        self.cache.set("key", "value")
        self.assertTrue(self.cache.delete("key"))
        self.assertIsNone(self.cache.get("key"))

    def test_clear_removes_only_this_cache_instances_keys(self):
        other = RedisCache(redis_url=self.redis_url, key_prefix=f"hayavega:cache:test:{uuid.uuid4()}:")
        self.addCleanup(other.clear)
        self.cache.set("key", "mine")
        other.set("key", "theirs")

        self.cache.clear()

        self.assertIsNone(self.cache.get("key"))
        self.assertEqual(other.get("key"), "theirs")

    def test_two_instances_with_the_same_prefix_share_state(self):
        """The scenario this backend exists for: two Hayavega processes behind
        a load balancer, both pointed at the same Redis, see each other's
        writes."""
        other = RedisCache(redis_url=self.redis_url, key_prefix=self.prefix)
        self.cache.set("key", "written by instance a")
        self.assertEqual(other.get("key"), "written by instance a")

    def test_get_or_set_computes_once_on_miss(self):
        calls = []

        def factory():
            calls.append(1)
            return "computed"

        self.assertEqual(self.cache.get_or_set("key", factory), "computed")
        self.assertEqual(self.cache.get_or_set("key", factory), "computed")
        self.assertEqual(len(calls), 1)

    def test_cache_from_settings_builds_a_working_redis_cache(self):
        cache = cache_from_settings({"BACKEND": "redis", "REDIS_URL": self.redis_url, "KEY_PREFIX": self.prefix})
        self.addCleanup(cache.clear)
        cache.set("key", "built from settings")
        self.assertEqual(cache.get("key"), "built from settings")
        self.assertEqual(cache.get("key"), self.cache.get("key"))  # same prefix as self.cache -> shared keyspace


if __name__ == "__main__":
    unittest.main()
