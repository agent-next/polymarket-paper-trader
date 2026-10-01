"""In-process per-client-IP fixed-window rate limiter."""
from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

_PRUNE_AT = 10_000  # tracked keys before expired windows are swept


class RateLimiter:
    """Allow `limit` hits per `window` seconds per (scope, client). limit <= 0 disables."""

    def __init__(self, limit: int, window: float = 60.0, trust_proxy: bool = False):
        self.limit = limit
        self.window = window
        self.trust_proxy = trust_proxy
        self._hits: dict[tuple[str, str], tuple[float, int]] = {}
        self._lock = threading.Lock()

    def client_ip(self, request: Request) -> str:
        if self.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                # The last entry is the one our own proxy appended; earlier ones are client-supplied.
                return forwarded.split(",")[-1].strip()
        return request.client.host if request.client else "unknown"

    def allow(self, scope: str, client: str, now: float | None = None) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) >= _PRUNE_AT:
                self._hits = {k: v for k, v in self._hits.items() if now - v[0] < self.window}
            start, count = self._hits.get((scope, client), (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            if count >= self.limit:
                return False
            self._hits[(scope, client)] = (start, count + 1)
            return True


def rate_limit(scope: str):
    """Route dependency: 429 with a RATE_LIMITED envelope once the client is over its limit."""

    def dependency(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        if not limiter.allow(scope, limiter.client_ip(request)):
            raise HTTPException(429, detail={
                "error": "Too many requests, retry later",
                "code": "RATE_LIMITED",
            })

    return dependency
