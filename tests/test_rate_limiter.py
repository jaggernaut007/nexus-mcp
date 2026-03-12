"""Tests for token bucket rate limiter (Phase 5e)."""

import threading
import time

from nexus_mcp.security.rate_limiter import TOOL_RATE_OVERRIDES, TokenBucketRateLimiter


def test_allows_within_rate():
    """Requests within rate are allowed."""
    rl = TokenBucketRateLimiter(default_rate=100.0, default_burst=10)
    for _ in range(10):
        assert rl.try_acquire("test") is True


def test_rejects_over_burst():
    """Requests exceeding burst are rejected."""
    rl = TokenBucketRateLimiter(default_rate=1.0, default_burst=3)
    assert rl.try_acquire("test") is True
    assert rl.try_acquire("test") is True
    assert rl.try_acquire("test") is True
    assert rl.try_acquire("test") is False


def test_burst_refills_over_time():
    """Tokens refill over time after being consumed."""
    rl = TokenBucketRateLimiter(default_rate=100.0, default_burst=2)
    # Consume all tokens
    assert rl.try_acquire("test") is True
    assert rl.try_acquire("test") is True
    assert rl.try_acquire("test") is False
    # Wait for refill
    time.sleep(0.05)  # 100 tokens/s * 0.05s = 5 tokens
    assert rl.try_acquire("test") is True


def test_per_key_isolation():
    """Different keys have independent buckets."""
    rl = TokenBucketRateLimiter(default_rate=1.0, default_burst=1)
    assert rl.try_acquire("a") is True
    assert rl.try_acquire("a") is False
    assert rl.try_acquire("b") is True  # separate bucket


def test_retry_after_positive_when_limited():
    """retry_after returns positive value when rate limited."""
    rl = TokenBucketRateLimiter(default_rate=1.0, default_burst=1)
    rl.try_acquire("test")
    rl.try_acquire("test")  # now limited
    retry = rl.get_retry_after("test")
    assert retry > 0


def test_retry_after_zero_when_available():
    """retry_after returns 0 when tokens are available."""
    rl = TokenBucketRateLimiter(default_rate=10.0, default_burst=5)
    retry = rl.get_retry_after("test")
    assert retry == 0.0


def test_tool_rate_overrides_exist():
    """Expected tool rate overrides are defined."""
    assert "index" in TOOL_RATE_OVERRIDES
    assert "search" in TOOL_RATE_OVERRIDES
    assert "remember" in TOOL_RATE_OVERRIDES
    # index should have very low rate
    rate, burst = TOOL_RATE_OVERRIDES["index"]
    assert rate < 1.0


def test_tool_specific_rates():
    """Rate limiter uses tool-specific rates from overrides."""
    rl = TokenBucketRateLimiter(default_rate=100.0, default_burst=100)
    # index has burst of 2
    assert rl.try_acquire("index") is True
    assert rl.try_acquire("index") is True
    assert rl.try_acquire("index") is False


def test_thread_safety():
    """Rate limiter is safe under concurrent access."""
    rl = TokenBucketRateLimiter(default_rate=1000.0, default_burst=100)
    results = []

    def worker():
        for _ in range(50):
            results.append(rl.try_acquire("test"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 200
    # At least 100 should succeed (burst=100)
    assert sum(results) >= 100


def test_default_key():
    """Default key 'default' works."""
    rl = TokenBucketRateLimiter(default_rate=10.0, default_burst=5)
    assert rl.try_acquire() is True


def test_high_rate_sustained():
    """High-rate limiter allows sustained traffic."""
    rl = TokenBucketRateLimiter(default_rate=1000.0, default_burst=50)
    # Burst of 50
    for _ in range(50):
        assert rl.try_acquire("test") is True
