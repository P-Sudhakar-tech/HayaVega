"""Opt-in, in-process request metrics: counts, latency distribution, and error rate."""

from __future__ import annotations

from threading import Lock

from .response import Response
from .routing import path

DEFAULT_BUCKETS_MS: tuple[float, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)


class MetricsRegistry:
    """Thread-safe in-process counters for request count, latency, and errors.

    Pass an instance as ``metrics=`` to ``create_application`` to record one
    observation per completed request; applications that do not pass one see
    no behavior change and no overhead, matching the opt-in pattern Hayavega
    Signals uses. Requests are labeled by the matched route pattern rather than
    the raw request path, so cardinality stays bounded by the number of
    registered routes; unmatched requests share a single ``unmatched`` label.
    """

    def __init__(self, *, buckets_ms: tuple[float, ...] = DEFAULT_BUCKETS_MS) -> None:
        self._buckets_ms = tuple(sorted(buckets_ms))
        self._lock = Lock()
        self._counts: dict[tuple[str, str, int], int] = {}
        self._duration_sum: dict[tuple[str, str], float] = {}
        self._duration_count: dict[tuple[str, str], int] = {}
        self._bucket_counts: dict[tuple[str, str], list[int]] = {}

    def record(self, *, method: str, path: str, status: int, duration_ms: float) -> None:
        key = (method, path)
        bucket_index = next((i for i, bound in enumerate(self._buckets_ms) if duration_ms <= bound), len(self._buckets_ms))
        with self._lock:
            counts_key = (method, path, status)
            self._counts[counts_key] = self._counts.get(counts_key, 0) + 1
            self._duration_sum[key] = self._duration_sum.get(key, 0.0) + duration_ms
            self._duration_count[key] = self._duration_count.get(key, 0) + 1
            buckets = self._bucket_counts.setdefault(key, [0] * (len(self._buckets_ms) + 1))
            buckets[bucket_index] += 1

    def snapshot(self) -> dict:
        """Return a plain-data copy of current counters; safe from any thread."""
        with self._lock:
            return {
                "counts": dict(self._counts),
                "duration_sum": dict(self._duration_sum),
                "duration_count": dict(self._duration_count),
                "bucket_counts": {key: list(value) for key, value in self._bucket_counts.items()},
                "buckets_ms": self._buckets_ms,
            }

    def error_rate(self) -> float:
        """Fraction of all recorded requests that returned a 5xx status."""
        with self._lock:
            total = sum(self._counts.values())
            if total == 0:
                return 0.0
            errors = sum(count for (_, _, status), count in self._counts.items() if status >= 500)
            return errors / total

    def render_prometheus(self) -> str:
        """Render counters in Prometheus text exposition format."""
        snapshot = self.snapshot()
        lines = [
            "# HELP hayavega_requests_total Total requests handled, labeled by method, route, and status.",
            "# TYPE hayavega_requests_total counter",
        ]
        for (method, route, status), count in sorted(snapshot["counts"].items()):
            lines.append(f'hayavega_requests_total{{method="{method}",path="{route}",status="{status}"}} {count}')
        lines += [
            "# HELP hayavega_request_duration_ms Request duration in milliseconds, labeled by method and route.",
            "# TYPE hayavega_request_duration_ms histogram",
        ]
        for key, buckets in sorted(snapshot["bucket_counts"].items()):
            method, route = key
            cumulative = 0
            for bound, count in zip(snapshot["buckets_ms"], buckets):  # noqa: B905 -- buckets has one extra overflow entry by design; strict=True would always raise here.
                cumulative += count
                lines.append(f'hayavega_request_duration_ms_bucket{{method="{method}",path="{route}",le="{bound}"}} {cumulative}')
            cumulative += buckets[-1]
            lines.append(f'hayavega_request_duration_ms_bucket{{method="{method}",path="{route}",le="+Inf"}} {cumulative}')
            lines.append(f'hayavega_request_duration_ms_sum{{method="{method}",path="{route}"}} {snapshot["duration_sum"][key]}')
            lines.append(f'hayavega_request_duration_ms_count{{method="{method}",path="{route}"}} {snapshot["duration_count"][key]}')
        return "\n".join(lines) + "\n"


def metrics_routes(registry: MetricsRegistry):
    """Return an unauthenticated ``/metrics`` endpoint in Prometheus text format."""

    def metrics(_request):
        return Response(registry.render_prometheus(), content_type="text/plain; version=0.0.4; charset=utf-8")

    return (path("metrics", metrics),)
