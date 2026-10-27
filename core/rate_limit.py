"""トークンバケット方式のレート制限(テナント別)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class _Bucket:
    capacity: float
    tokens: float
    refill_per_sec: float
    last: float


class RateLimiter:
    """テナント単位のトークンバケット.

    capacity: バースト許容量, refill_per_sec: 毎秒回復レート.
    now_fn を差し替えるとテストで時間を制御できる。
    """

    def __init__(self, capacity: float = 10, refill_per_sec: float = 1.0,
                 now_fn: Callable[[], float] = time.monotonic) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._now = now_fn
        self._buckets: dict[str, _Bucket] = {}

    def _bucket(self, key: str) -> _Bucket:
        b = self._buckets.get(key)
        if b is None:
            b = _Bucket(self.capacity, self.capacity, self.refill_per_sec, self._now())
            self._buckets[key] = b
        return b

    def allow(self, key: str, cost: float = 1.0) -> bool:
        b = self._bucket(key)
        now = self._now()
        elapsed = max(0.0, now - b.last)
        b.tokens = min(b.capacity, b.tokens + elapsed * b.refill_per_sec)
        b.last = now
        if b.tokens >= cost:
            b.tokens -= cost
            return True
        return False
