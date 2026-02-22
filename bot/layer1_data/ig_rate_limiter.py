"""IG Markets rate limiter — sliding-window limiter per endpoint category.

IG enforces different rate limits per API category:
  - data: 60 requests/minute (market prices, details)
  - trading: 15 requests/minute (orders, positions)
  - historical: 30 requests/minute (price history)

This limiter proactively delays requests when the window is full,
preventing 403 responses from IG.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

# Requests per 60-second window
_LIMITS: dict[str, int] = {
    "data": 60,
    "trading": 15,
    "historical": 30,
}

_WINDOW_S = 60.0


class IGRateLimiter:
    """Sliding-window rate limiter for IG API categories."""

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = {
            cat: deque() for cat in _LIMITS
        }

    async def acquire(self, category: str = "data") -> None:
        """Wait until a request slot is available in the given category."""
        limit = _LIMITS.get(category, 60)
        window = self._windows.setdefault(category, deque())

        while True:
            now = time.monotonic()

            # Purge timestamps older than the window
            while window and window[0] <= now - _WINDOW_S:
                window.popleft()

            if len(window) < limit:
                window.append(now)
                return

            # Wait until the oldest request falls out of the window
            wait_time = window[0] + _WINDOW_S - now + 0.05
            await asyncio.sleep(wait_time)
