"""Small in-memory helpers."""

import time
from functools import wraps


class TTLCache:
    """Key-value cache whose entries expire after ttl seconds."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data = {}

    def get(self, key):
        item = self._data.get(key)
        if item and time.time() - item[1] < self.ttl:
            return item[0]
        return None

    def set(self, key, value) -> None:
        self._data[key] = (value, time.time())


def rate_limit(max_calls: int, per_seconds: float):
    """Decorator that rejects calls above max_calls inside the window."""

    def decorator(fn):
        calls = []

        @wraps(fn)
        def wrapper(*args, **kwargs):
            now = time.time()
            calls[:] = [t for t in calls if now - t < per_seconds]
            if len(calls) >= max_calls:
                raise RuntimeError("rate limit exceeded")
            calls.append(now)
            return fn(*args, **kwargs)

        return wrapper

    return decorator
