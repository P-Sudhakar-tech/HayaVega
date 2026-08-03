"""Opt-in production HTTP hardening for Mangoo's WSGI runtime."""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, replace
import logging
from ipaddress import ip_address, ip_network
from threading import Lock
import time

from .response import Response


LOGGER = logging.getLogger("mangoo.audit")


@dataclass(frozen=True)
class HardeningOptions:
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    rate_limit: int = 120
    rate_window_seconds: int = 60
    enable_security_headers: bool = True
    force_https: bool = False
    trust_proxy_headers: bool = False
    trusted_proxy_ips: tuple[str, ...] = ()
    max_request_bytes: int = 1_048_576
    rate_limit_max_clients: int = 10_000

    @classmethod
    def from_settings(cls, *, allowed_hosts=(), security=None):
        security = security or {}
        return cls(
            allowed_hosts=tuple(allowed_hosts),
            allowed_origins=tuple(security.get("ALLOWED_ORIGINS", ())),
            rate_limit=int(security.get("RATE_LIMIT", 120)),
            rate_window_seconds=int(security.get("RATE_WINDOW_SECONDS", 60)),
            enable_security_headers=bool(security.get("SECURITY_HEADERS", True)),
            force_https=bool(security.get("FORCE_HTTPS", False)),
            trust_proxy_headers=bool(security.get("TRUST_PROXY_HEADERS", False)),
            trusted_proxy_ips=tuple(security.get("TRUSTED_PROXY_IPS", ())),
            max_request_bytes=int(security.get("MAX_REQUEST_BYTES", 1_048_576)),
            rate_limit_max_clients=int(security.get("RATE_LIMIT_MAX_CLIENTS", 10_000)),
        )


class RateLimiter:
    """Thread-safe, in-memory fixed-window request limiter for one process."""
    def __init__(self, limit: int, window_seconds: int, max_clients: int = 10_000):
        if limit < 1 or window_seconds < 1 or max_clients < 1:
            raise ValueError("Rate limit, window, and max_clients must be positive integers.")
        self.limit, self.window_seconds, self.max_clients = limit, window_seconds, max_clients
        self._requests = OrderedDict()
        self._lock = Lock()

    def _prune(self, now: float) -> None:
        """Drop globally idle keys before applying the bounded LRU policy."""
        expired = []
        for key, entries in self._requests.items():
            while entries and entries[0] <= now - self.window_seconds:
                entries.popleft()
            if not entries:
                expired.append(key)
        for key in expired:
            del self._requests[key]

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            entries = self._requests.get(key)
            if entries is None:
                if len(self._requests) >= self.max_clients:
                    self._requests.popitem(last=False)
                entries = deque()
                self._requests[key] = entries
            else:
                self._requests.move_to_end(key)
            while entries and entries[0] <= now - self.window_seconds:
                entries.popleft()
            if len(entries) >= self.limit:
                return False
            entries.append(now)
            return True

    def stats(self) -> dict[str, int]:
        """Return non-sensitive limiter capacity information for monitoring."""
        with self._lock:
            return {"tracked_clients": len(self._requests), "max_clients": self.max_clients}


class Hardening:
    """Validate requests and decorate responses using a single policy object."""
    def __init__(self, options: HardeningOptions):
        self.options = options
        self.limiter = RateLimiter(options.rate_limit, options.rate_window_seconds, options.rate_limit_max_clients)
        try:
            self._trusted_proxy_networks = tuple(ip_network(value, strict=False) for value in options.trusted_proxy_ips)
        except ValueError as error:
            raise ValueError("TRUSTED_PROXY_IPS must contain IP addresses or CIDR networks.") from error

    def _is_trusted_proxy(self, request) -> bool:
        if not self.options.trust_proxy_headers or not self._trusted_proxy_networks:
            return False
        try:
            source = ip_address(request.remote_addr)
        except ValueError:
            return False
        return any(source in network for network in self._trusted_proxy_networks)

    def reject_content_length(self, content_length: int) -> Response | None:
        if content_length > self.options.max_request_bytes:
            return Response("<h1>413 Payload Too Large</h1>", status=413)
        return None

    def reject(self, request) -> Response | None:
        host = request.headers.get("Host", "").split(":", 1)[0].lower()
        if self.options.allowed_hosts and "*" not in self.options.allowed_hosts and host not in self.options.allowed_hosts:
            return Response("<h1>400 Bad Request</h1>", status=400)
        client = request.remote_addr or "unknown"
        trusted_proxy = self._is_trusted_proxy(request)
        if trusted_proxy:
            client = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip() or client
        if not self.limiter.allow(client):
            return Response("<h1>429 Too Many Requests</h1>", status=429, headers=(("Retry-After", str(self.options.rate_window_seconds)),))
        forwarded_scheme = request.headers.get("X-Forwarded-Proto", "").lower()
        secure = request.is_secure or (trusted_proxy and forwarded_scheme == "https")
        if self.options.force_https and not secure:
            host_header = request.headers.get("Host", "")
            query = f"?{request.raw_query}" if request.raw_query else ""
            return Response("", status=308, headers=(("Location", f"https://{host_header}{request.path}{query}"),))
        return None

    def preflight(self, request) -> Response | None:
        if request.method == "OPTIONS" and request.headers.get("Origin") in self.options.allowed_origins:
            return Response("", status=204)
        return None

    def finalize(self, request, response: Response, *, request_id: str | None = None, duration_ms: float | None = None) -> Response:
        headers = list(response.headers)
        existing = {name.lower() for name, _ in headers}
        if self.options.enable_security_headers:
            defaults = {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "strict-origin-when-cross-origin",
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline' https://unpkg.com; script-src 'self' 'unsafe-inline' https://unpkg.com",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-origin",
            }
            headers.extend((name, value) for name, value in defaults.items() if name.lower() not in existing)
            forwarded_scheme = request.headers.get("X-Forwarded-Proto", "").lower()
            if (request.is_secure or (self._is_trusted_proxy(request) and forwarded_scheme == "https")) and "strict-transport-security" not in existing:
                headers.append(("Strict-Transport-Security", "max-age=31536000; includeSubDomains"))
        origin = request.headers.get("Origin")
        if origin and origin in self.options.allowed_origins:
            headers.extend((("Access-Control-Allow-Origin", origin), ("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS"), ("Access-Control-Allow-Headers", "Authorization, Content-Type, X-CSRF-Token"), ("Vary", "Origin")))
        if request_id and "x-request-id" not in existing:
            headers.append(("X-Request-ID", request_id))
        LOGGER.info(
            "request id=%s method=%s path=%s status=%s duration_ms=%.2f", request_id, request.method, request.path, response.status, duration_ms or 0,
            extra={"request_id": request_id, "method": request.method, "path": request.path, "status": response.status, "duration_ms": duration_ms or 0},
        )
        return replace(response, headers=tuple(headers))
