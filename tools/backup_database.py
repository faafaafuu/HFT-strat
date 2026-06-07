from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import load_settings
from app.data.database import Database


async def main() -> None:
    settings = load_settings()
    url = settings.database.url
    backups_dir = Path(settings.storage.backups_dir)
    docker_db = Path("/app/data/bot.sqlite3")
    local_db = Path("data/bot.sqlite3")
    if url.endswith(str(docker_db)) and not docker_db.exists():
        url = f"sqlite+aiosqlite:///{local_db}"
        backups_dir = Path("backups")
    database = Database(url, backups_dir=backups_dir)
    path = await database.backup_sqlite("manual")
    if path is None:
        print("No SQLite database found to back up.")
        return
    print(Path(path))


if __name__ == "__main__":
    asyncio.run(main())
