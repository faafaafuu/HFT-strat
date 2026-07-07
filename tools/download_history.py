from __future__ import annotations

import argparse
import asyncio

from app.backtesting.data_loader import download_bybit_history
from app.config import load_settings
from app.data.database import Database


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--db", default=None, help="Override database URL, e.g. sqlite+aiosqlite:///data/bot.sqlite3")
    args = parser.parse_args()
    settings = load_settings()
    database = Database(args.db or settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    try:
        count = await download_bybit_history(
            database,
            symbol=args.symbol.upper(),
            timeframe=args.timeframe,
            days=args.days,
        )
        print(f"stored_candles={count}")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(_main())
