"""Rate limiting — protects the public demo endpoint (and your free-tier quota).

The judge-facing demo page is public and login-free (rubric requires no login),
so `/chat` is exposed to the open internet. Each turn spends a Gemini call on a
free-tier key, so an unbounded endpoint is both an abuse vector and a quota/cost
risk. Two cheap in-memory fixed-window limiters guard it:

* PER-CLIENT — caps how fast one IP can ask (stops a single abuser/bot).
* GLOBAL — caps total turns across everyone in a window (protects the shared
  daily quota even under many IPs).

Fixed-window (not a token bucket) on purpose: it's a handful of lines, has no
background tasks, and is precise enough for a demo. Caveat documented for honesty:
state is per-process, so on Cloud Run with N instances the effective limit is
~N× these numbers. For a demo that's fine; a multi-instance production system
would back this with Redis (the dashboard's `lib/rate-limit` does the same).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of one limiter check.

    `allowed` gates the request; when False, `retry_after_seconds` tells the
    caller (and the client, via a Retry-After header) how long until the window
    resets.
    """

    allowed: bool
    retry_after_seconds: int = 0


class FixedWindowLimiter:
    """Thread-safe fixed-window counter keyed by an arbitrary string.

    Counts hits per key within a rolling window of `window_seconds`. When the
    count exceeds `max_hits`, further hits are rejected until the window rolls
    over. A single lock guards the dict — contention is negligible at demo scale.
    """

    def __init__(self, max_hits: int, window_seconds: int) -> None:
        if max_hits < 1 or window_seconds < 1:
            raise ValueError("max_hits and window_seconds must be >= 1")
        self._max_hits = max_hits
        self._window = window_seconds
        # key -> (window_start_epoch, hit_count)
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> RateLimitResult:
        """Record a hit for `key` and report whether it's within the limit.

        `now` is injectable so the behavior is deterministically testable.
        """
        current = time.monotonic() if now is None else now
        with self._lock:
            window_start, count = self._buckets.get(key, (current, 0))

            # Window expired -> start a fresh one.
            if current - window_start >= self._window:
                window_start, count = current, 0

            if count >= self._max_hits:
                retry_after = max(1, int(self._window - (current - window_start)))
                return RateLimitResult(allowed=False, retry_after_seconds=retry_after)

            self._buckets[key] = (window_start, count + 1)
            return RateLimitResult(allowed=True)


if __name__ == "__main__":
    # Smoke test: 3 hits allowed per 10s window, 4th blocked, then allowed again
    # after the window rolls (using injected time so it's instant + deterministic).
    limiter = FixedWindowLimiter(max_hits=3, window_seconds=10)
    t0 = 1000.0
    for i in range(4):
        r = limiter.check("ip-1", now=t0 + i)  # 4 hits inside the same window
        print(f"hit {i + 1}: allowed={r.allowed} retry_after={r.retry_after_seconds}")
    r = limiter.check("ip-1", now=t0 + 11)  # window rolled over
    print(f"after window: allowed={r.allowed}")
    r = limiter.check("ip-2", now=t0 + 1)  # a different key is independent
    print(f"other key: allowed={r.allowed}")
