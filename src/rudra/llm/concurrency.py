"""Per-endpoint concurrency caps.

A limit of 1 means "no gating at all": `semaphore_for` returns None, so a
caller adds nothing and the serial path stays byte-identical to before.
Semaphores are built lazily, on first request, and cached so every caller
for one endpoint shares the same object. Since Python 3.12 an
`asyncio.Semaphore` binds to a loop when first awaited, not at
construction, so building it from this synchronous method is correct.
"""

from __future__ import annotations

import asyncio


class EndpointLimiter:
    def __init__(self, limits: dict[str, int]) -> None:
        self._limits = dict(limits)
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def limit_for(self, base_url: str | None) -> int:
        if base_url is None:
            return 1
        return self._limits.get(base_url, 1)

    def semaphore_for(self, base_url: str | None) -> asyncio.Semaphore | None:
        limit = self.limit_for(base_url)
        if base_url is None or limit <= 1:
            return None
        sem = self._semaphores.get(base_url)
        if sem is None:
            sem = self._semaphores[base_url] = asyncio.Semaphore(limit)
        return sem
