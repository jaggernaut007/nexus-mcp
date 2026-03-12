"""Token bucket rate limiter for Nexus-MCP tools.

Off by default for stdio transport. Per-key (tool name) rate limiting.
"""

import threading
import time
from dataclasses import dataclass
from typing import Dict


@dataclass
class _Bucket:
    """Internal token bucket state for a single key."""

    tokens: float
    last_refill: float
    rate: float
    burst: int

    def try_acquire(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def get_retry_after(self) -> float:
        """Seconds until at least one token is available."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        current_tokens = min(self.burst, self.tokens + elapsed * self.rate)
        if current_tokens >= 1.0:
            return 0.0
        deficit = 1.0 - current_tokens
        return deficit / self.rate if self.rate > 0 else float("inf")


# Default per-tool rate overrides
TOOL_RATE_OVERRIDES: Dict[str, tuple[float, int]] = {
    "index": (0.1, 2),          # 1 per 10s, burst 2
    "search": (10.0, 20),       # 10/s, burst 20
    "recall": (10.0, 20),
    "explain": (10.0, 20),
    "remember": (5.0, 10),      # 5/s, burst 10
    "forget": (5.0, 10),
}


class TokenBucketRateLimiter:
    """Thread-safe token bucket rate limiter with per-key tracking."""

    def __init__(self, default_rate: float = 10.0, default_burst: int = 20):
        self.default_rate = default_rate
        self.default_burst = default_burst
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _get_bucket(self, key: str) -> _Bucket:
        """Get or create a bucket for a key."""
        if key not in self._buckets:
            rate, burst = TOOL_RATE_OVERRIDES.get(
                key, (self.default_rate, self.default_burst)
            )
            self._buckets[key] = _Bucket(
                tokens=float(burst),
                last_refill=time.monotonic(),
                rate=rate,
                burst=burst,
            )
        return self._buckets[key]

    def try_acquire(self, key: str = "default") -> bool:
        """Try to acquire a token for the given key.

        Returns True if allowed, False if rate limited.
        """
        with self._lock:
            return self._get_bucket(key).try_acquire()

    def get_retry_after(self, key: str = "default") -> float:
        """Get seconds until next token is available for the key."""
        with self._lock:
            return self._get_bucket(key).get_retry_after()
