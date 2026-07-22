import asyncio
from pathlib import Path

import pytest

from app.config import DatabaseConfig, Settings, StorageConfig
from app.data.database import Database
from app.jobs.models import RUN_BACKTEST
from app.jobs.queue import JobQueue
from app.jobs.worker import JobWorker


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database=DatabaseConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'bot.sqlite3'}"),
        storage=StorageConfig(
            data_dir=str(tmp_path / "data"),
            logs_dir=str(tmp_path / "logs"),
            backups_dir=str(tmp_path / "backups"),
        ),
    )


@pytest.mark.asyncio
async def test_running_status_is_visible_while_the_job_works(tmp_path: Path) -> None:
    """A sweep runs for minutes; the dashboard must not show it as still queued."""
    settings = _settings(tmp_path)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    await JobQueue(database).enqueue(RUN_BACKTEST, {"strategy_key": "x", "symbol": "BTCUSDT"})

    worker = JobWorker(database, settings)
    seen: list[str] = []
    started = asyncio.Event()

    async def slow_job(job_type: str, params: dict) -> dict:
        started.set()
        await asyncio.sleep(0.2)
        return {"ok": True}

    worker._run_job = slow_job  # type: ignore[method-assign]
    task = asyncio.create_task(worker.run_once())
    await started.wait()

    jobs = await JobQueue(database).recent()
    seen.append(jobs[0]["status"])
    await task
    jobs = await JobQueue(database).recent()
    seen.append(jobs[0]["status"])
    await database.close()

    assert seen == ["RUNNING", "DONE"]


@pytest.mark.asyncio
async def test_failed_job_records_the_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    await JobQueue(database).enqueue(RUN_BACKTEST, {"strategy_key": "x", "symbol": "BTCUSDT"})

    worker = JobWorker(database, settings)

    async def failing_job(job_type: str, params: dict) -> dict:
        raise RuntimeError("нет свечей")

    worker._run_job = failing_job  # type: ignore[method-assign]
    await worker.run_once()

    jobs = await JobQueue(database).recent()
    await database.close()

    assert jobs[0]["status"] == "FAILED"
    assert "нет свечей" in jobs[0]["error"]
