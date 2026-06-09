from __future__ import annotations

import asyncio

from app.config import load_settings
from app.data.database import Database
from app.jobs.worker import JobWorker


async def _main() -> None:
    settings = load_settings()
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    try:
        result = await JobWorker(database, settings).run_once()
        print(result)
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(_main())
