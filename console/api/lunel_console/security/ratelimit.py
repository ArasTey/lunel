"""In-memory sliding-window rate limiter (per IP + bucket).

Good enough for a single control-plane process; the class boundary allows
swapping in a Redis backend for multi-node deployments.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

try:
    from fastapi import HTTPException
except ImportError:  # pragma: no cover
    HTTPException = None


@dataclass
class Rule:
    name: str
    limit: int
    window_seconds: float


RULES = {
    "auth": Rule("auth", limit=30, window_seconds=60),
    "api_write": Rule("api_write", limit=240, window_seconds=60),
    "api_read": Rule("api_read", limit=1200, window_seconds=60),
    "public": Rule("public", limit=300, window_seconds=60),
}


class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, rule: Rule) -> None:
        now = time.monotonic()
        with self._lock:
            window = self._hits[key]
            while window and window[0] <= now - rule.window_seconds:
                window.popleft()
            if len(window) >= rule.limit:
                retry_after = int(rule.window_seconds - (now - window[0])) + 1
                if HTTPException is not None:
                    raise HTTPException(
                        status_code=429,
                        detail="rate limit exceeded",
                        headers={"Retry-After": str(retry_after)},
                    )
                raise RuntimeError("rate limit exceeded")
            window.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
