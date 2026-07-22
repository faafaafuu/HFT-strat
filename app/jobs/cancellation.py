from __future__ import annotations

import time

from app.data.database import Database
from app.data.repositories import JobRepository
from app.jobs.models import JobCancelled

# A sweep checks between every combination, which on a fast one is many times a second.
# Polling SQLite that often would cost more than the work itself.
POLL_INTERVAL_SECONDS = 2.0


class CancellationToken:
    """Cooperative stop signal for a long job, read from the jobs table.

    The web process cannot interrupt the worker, so cancelling is a flag the job itself
    looks at. Nothing is forced: a job stops only where stopping leaves the data sane.
    """

    def __init__(
        self,
        database: Database,
        job_id: int,
        *,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.database = database
        self.job_id = job_id
        self.poll_interval = poll_interval_seconds
        self._last_poll = 0.0
        self._cancelled = False

    async def cancelled(self) -> bool:
        if self._cancelled:
            return True
        now = time.monotonic()
        if now - self._last_poll < self.poll_interval:
            return False
        self._last_poll = now
        async with self.database.session() as session:
            status = await JobRepository(session).status(self.job_id)
        self._cancelled = status in {"CANCELLING", "CANCELLED"}
        return self._cancelled

    async def raise_if_cancelled(self) -> None:
        if await self.cancelled():
            raise JobCancelled(f"Задача #{self.job_id} отменена")
