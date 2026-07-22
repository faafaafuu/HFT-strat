import asyncio
from pathlib import Path

import pytest

from app.config import DatabaseConfig, Settings, StorageConfig
from app.data.database import Database
from app.data.repositories import JobRepository
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

    async def slow_job(job_type: str, params: dict, cancellation=None) -> dict:
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

    async def failing_job(job_type: str, params: dict, cancellation=None) -> dict:
        raise RuntimeError("нет свечей")

    worker._run_job = failing_job  # type: ignore[method-assign]
    await worker.run_once()

    jobs = await JobQueue(database).recent()
    await database.close()

    assert jobs[0]["status"] == "FAILED"
    assert "нет свечей" in jobs[0]["error"]


@pytest.mark.asyncio
async def test_queued_job_is_cancelled_before_it_ever_starts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    job = await JobQueue(database).enqueue(RUN_BACKTEST, {"strategy_key": "x", "symbol": "BTCUSDT"})

    async with database.session() as session:
        outcome = await JobRepository(session).request_cancel(job["id"])

    worker = JobWorker(database, settings)
    processed = await worker.run_once()
    jobs = await JobQueue(database).recent()
    await database.close()

    assert outcome == "cancelled"
    # The worker only ever claims PENDING rows, so a cancelled one is simply never picked up.
    assert processed == {"processed": 0}
    assert jobs[0]["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_running_job_stops_at_its_next_checkpoint(tmp_path: Path) -> None:
    """Cancelling a running job is a request: the worker owns the loop and stops itself."""
    settings = _settings(tmp_path)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    job = await JobQueue(database).enqueue(RUN_BACKTEST, {"strategy_key": "x", "symbol": "BTCUSDT"})

    worker = JobWorker(database, settings)
    worker.cancellation_poll_seconds = 0.01
    started = asyncio.Event()
    checkpoints = 0

    async def long_job(job_type: str, params: dict, cancellation=None) -> dict:
        nonlocal checkpoints
        started.set()
        for _ in range(100):
            checkpoints += 1
            await cancellation.raise_if_cancelled()
            await asyncio.sleep(0.01)
        return {"finished": True}

    worker._run_job = long_job  # type: ignore[method-assign]
    task = asyncio.create_task(worker.run_once())
    await started.wait()

    async with database.session() as session:
        outcome = await JobRepository(session).request_cancel(job["id"])
    await task

    jobs = await JobQueue(database).recent()
    await database.close()

    assert outcome == "cancelling"
    assert jobs[0]["status"] == "CANCELLED"
    assert "отменена" in jobs[0]["error"]
    # It stopped early rather than running all 100 iterations.
    assert checkpoints < 100


@pytest.mark.asyncio
async def test_cancelling_a_finished_job_changes_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    job = await JobQueue(database).enqueue(RUN_BACKTEST, {"strategy_key": "x", "symbol": "BTCUSDT"})

    worker = JobWorker(database, settings)

    async def quick_job(job_type: str, params: dict, cancellation=None) -> dict:
        return {"ok": True}

    worker._run_job = quick_job  # type: ignore[method-assign]
    await worker.run_once()

    async with database.session() as session:
        outcome = await JobRepository(session).request_cancel(job["id"])
    jobs = await JobQueue(database).recent()
    await database.close()

    assert outcome == "finished"
    assert jobs[0]["status"] == "DONE"
