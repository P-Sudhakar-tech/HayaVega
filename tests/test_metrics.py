import unittest

from test_runtime import call

from hayavega import Response, path
from hayavega.metrics import MetricsRegistry, metrics_routes
from hayavega.runtime import create_application


class MetricsRegistryTests(unittest.TestCase):
    def test_record_tracks_counts_and_duration(self):
        registry = MetricsRegistry()
        registry.record(method="GET", path="/", status=200, duration_ms=12.5)
        registry.record(method="GET", path="/", status=200, duration_ms=7.5)
        snapshot = registry.snapshot()
        self.assertEqual(snapshot["counts"][("GET", "/", 200)], 2)
        self.assertEqual(snapshot["duration_count"][("GET", "/")], 2)
        self.assertEqual(snapshot["duration_sum"][("GET", "/")], 20.0)

    def test_error_rate_counts_only_5xx_statuses(self):
        registry = MetricsRegistry()
        registry.record(method="GET", path="/", status=200, duration_ms=1)
        registry.record(method="GET", path="/", status=404, duration_ms=1)
        registry.record(method="GET", path="/", status=500, duration_ms=1)
        self.assertAlmostEqual(registry.error_rate(), 1 / 3)

    def test_error_rate_is_zero_with_no_requests(self):
        self.assertEqual(MetricsRegistry().error_rate(), 0.0)

    def test_record_places_duration_in_correct_bucket(self):
        registry = MetricsRegistry(buckets_ms=(10, 100))
        registry.record(method="GET", path="/", status=200, duration_ms=5)
        registry.record(method="GET", path="/", status=200, duration_ms=50)
        registry.record(method="GET", path="/", status=200, duration_ms=500)
        buckets = registry.snapshot()["bucket_counts"][("GET", "/")]
        self.assertEqual(buckets, [1, 1, 1])

    def test_render_prometheus_includes_counters_and_histogram(self):
        registry = MetricsRegistry()
        registry.record(method="GET", path="/", status=200, duration_ms=12.5)
        text = registry.render_prometheus()
        self.assertIn('hayavega_requests_total{method="GET",path="/",status="200"} 1', text)
        self.assertIn("hayavega_request_duration_ms_bucket{", text)
        self.assertIn('hayavega_request_duration_ms_bucket{method="GET",path="/",le="+Inf"} 1', text)
        self.assertIn('hayavega_request_duration_ms_sum{method="GET",path="/"} 12.5', text)
        self.assertIn('hayavega_request_duration_ms_count{method="GET",path="/"} 1', text)


class MetricsIntegrationTests(unittest.TestCase):
    def test_create_application_records_metrics_for_matched_route(self):
        registry = MetricsRegistry()
        app = create_application([path("hello", lambda request: Response("hi"))], metrics=registry)
        call(app, "/hello/")
        snapshot = registry.snapshot()
        self.assertEqual(snapshot["counts"][("GET", "hello", 200)], 1)

    def test_create_application_labels_unmatched_routes(self):
        registry = MetricsRegistry()
        app = create_application([], metrics=registry)
        call(app, "/missing/")
        snapshot = registry.snapshot()
        self.assertEqual(snapshot["counts"][("GET", "unmatched", 404)], 1)

    def test_create_application_without_metrics_is_unaffected(self):
        app = create_application([path("", lambda request: Response("ok"))])
        captured, body = call(app, "/")
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(body, b"ok")

    def test_metrics_routes_exposes_prometheus_endpoint(self):
        registry = MetricsRegistry()
        app = create_application([path("hello", lambda request: Response("hi")), *metrics_routes(registry)], metrics=registry)
        call(app, "/hello/")
        captured, body = call(app, "/metrics")
        self.assertEqual(captured["status"], "200 OK")
        self.assertIn("text/plain", captured["headers"]["Content-Type"])
        self.assertIn('hayavega_requests_total{method="GET",path="hello",status="200"} 1', body.decode("utf-8"))

    def test_metrics_routes_are_excluded_from_openapi_document(self):
        registry = MetricsRegistry()
        app = create_application([*metrics_routes(registry)])
        captured, body = call(app, "/openapi.json")
        self.assertNotIn(b'"/metrics"', body)


if __name__ == "__main__":
    unittest.main()
