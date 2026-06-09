from __future__ import annotations

from typing import Any

from app.data.database import Database
from app.data.repositories import JobRepository


class JobQueue:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def enqueue(self, job_type: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self.database.session() as session:
            job = await JobRepository(session).create(job_type, params)
            return {"id": job.id, "job_type": job.job_type, "status": job.status}

    async def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            jobs = await JobRepository(session).list_recent(limit)
            return [
                {
                    "id": job.id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "error": job.error,
                }
                for job in jobs
            ]
