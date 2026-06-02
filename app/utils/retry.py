from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    attempts: int = 3,
    initial_delay: float = 1.0,
    multiplier: float = 2.0,
) -> T:
    delay = initial_delay
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - caller decides retry scope.
            last_exc = exc
            await asyncio.sleep(delay)
            delay *= multiplier
    if last_exc is None:
        raise RuntimeError("retry_async exhausted without capturing an exception")
    raise last_exc
