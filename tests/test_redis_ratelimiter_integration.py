"""Opt-in integration coverage; set HAYAVEGA_TEST_REDIS_URL to run it.

Unlike the PostgreSQL/MySQL integration tests, one of these tests (the
missing-dependency check) runs everywhere without a real Redis, since it only
needs `import redis` to fail. The rest require a real Redis instance and are
skipped unless HAYAVEGA_TEST_REDIS_URL is set, matching the DB integration
test pattern.
"""

import os
import sys
import time
import unittest
import uuid
from unittest.mock import patch

from hayavega.hardening import RateLimiter, RateLimiterBackendError, RedisRateLimiter


class RedisRateLimiterMissingDependencyTests(unittest.TestCase):
    def test_missing_redis_package_raises_backend_error(self):
        with patch.dict(sys.modules, {"redis": None}):
            with self.assertRaises(RateLimiterBackendError):
                RedisRateLimiter(limit=5, window_seconds=60)


@unittest.skipUnless(os.getenv("HAYAVEGA_TEST_REDIS_URL"), "Redis integration environment is not configured")
class RedisRateLimiterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.redis_url = os.environ["HAYAVEGA_TEST_REDIS_URL"]
        try:
            self.limiter = RedisRateLimiter(limit=3, window_seconds=2, redis_url=self.redis_url)
            # Confirm connectivity up front so a genuinely unreachable Redis
            # skips cleanly instead of failing every test in this class.
            self.limiter._client.ping()
        except RateLimiterBackendError as error:
            self.skipTest(str(error))
        except Exception as error:  # noqa: BLE001 - any connection failure means "skip", not "fail"
            self.skipTest(f"Redis at {self.redis_url} is not reachable: {error}")
        self.key = f"test-{uuid.uuid4()}"
        self.addCleanup(self.limiter._client.delete, f"hayavega:ratelimit:{self.key}")

    def test_allows_up_to_the_limit_then_denies(self):
        self.assertTrue(self.limiter.allow(self.key))
        self.assertTrue(self.limiter.allow(self.key))
        self.assertTrue(self.limiter.allow(self.key))
        self.assertFalse(self.limiter.allow(self.key))

    def test_window_expiry_resets_the_count(self):
        for _ in range(3):
            self.assertTrue(self.limiter.allow(self.key))
        self.assertFalse(self.limiter.allow(self.key))
        time.sleep(2.2)  # window_seconds=2
        self.assertTrue(self.limiter.allow(self.key))

    def test_stats_reports_configured_policy(self):
        self.assertEqual(self.limiter.stats(), {"limit": 3, "window_seconds": 2})

    def test_two_independent_instances_share_one_combined_limit(self):
        """This is the scenario this feature exists to fix.

        Two `RedisRateLimiter` objects here stand in for two separate Hayavega
        processes behind a load balancer, both pointed at the same Redis and
        the same rate-limit key. Requests are interleaved between them, and
        the *combined* total allowed across both must respect the configured
        limit of 3 -- not 3 per instance.

        Contrast: two independent in-process `RateLimiter` instances (the
        default backend) do NOT share state, so the same interleaved
        sequence against two `RateLimiter` objects would wrongly allow 6
        requests total (3 each) for a single client hammering two instances
        behind a load balancer -- exactly the silent multi-instance gap
        described in DEPLOYMENT.md that this Redis backend closes.
        """
        instance_a = RedisRateLimiter(limit=3, window_seconds=5, redis_url=self.redis_url)
        instance_b = RedisRateLimiter(limit=3, window_seconds=5, redis_url=self.redis_url)
        key = f"shared-{uuid.uuid4()}"
        self.addCleanup(instance_a._client.delete, f"hayavega:ratelimit:{key}")

        results = []
        for instance in (instance_a, instance_b, instance_a, instance_b, instance_a, instance_b):
            results.append(instance.allow(key))

        self.assertEqual(sum(results), 3)  # exactly the shared limit, not 3 per instance (6 total)

        # What the in-process backend would have wrongly allowed in the same
        # interleaved scenario: each RateLimiter only sees its own calls, so
        # both instances independently allow all 3 of their own requests.
        local_a = RateLimiter(limit=3, window_seconds=5)
        local_b = RateLimiter(limit=3, window_seconds=5)
        local_results = []
        for limiter in (local_a, local_b, local_a, local_b, local_a, local_b):
            local_results.append(limiter.allow(key))
        self.assertEqual(sum(local_results), 6)  # the bug: 2x the configured limit gets through
