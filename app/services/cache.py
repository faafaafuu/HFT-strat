from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class AsyncTTLCache(Generic[T]):
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, T]] = {}

    async def get_or_set(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        now = time.monotonic()
        cached = self._items.get(key)
        if cached is not None:
            expires_at, value = cached
            if expires_at > now:
                return value
        value = await factory()
        self._items[key] = (now + self.ttl_seconds, value)
        return value

    def invalidate(self, prefix: str | None = None) -> None:
        if prefix is None:
            self._items.clear()
            return
        for key in list(self._items):
            if key.startswith(prefix):
                self._items.pop(key, None)

