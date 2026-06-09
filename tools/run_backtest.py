from __future__ import annotations

import argparse
import asyncio
import json

from app.backtesting.engine import BacktestEngine
from app.config import load_settings
from app.data.database import Database


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    settings = load_settings()
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    try:
        result = await BacktestEngine(database, settings).run(
            strategy_key=args.strategy,
            symbol=args.symbol.upper(),
            timeframe=args.timeframe,
            days=args.days,
        )
        print(json.dumps(result["metrics"], indent=2, default=str))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(_main())
