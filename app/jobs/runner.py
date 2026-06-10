from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from app.config import load_settings
from app.data.database import Database
from app.jobs.worker import JobWorker
from app.logger import setup_logging
from app.main import _apply_runtime_settings


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.app.log_level)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    await _apply_runtime_settings(database, settings)
    worker = JobWorker(database, settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    interval = float(os.getenv("JOB_WORKER_INTERVAL_SEC", "5"))
    heartbeat_path = Path(settings.storage.data_dir) / "worker_heartbeat"
    try:
        while not stop.is_set():
            heartbeat_path.write_text(str(asyncio.get_running_loop().time()))
            result = await worker.run_once()
            if result.get("processed", 0) == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=interval)
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
