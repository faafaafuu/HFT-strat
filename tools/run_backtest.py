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
    parser.add_argument("--db", default=None, help="Override database URL, e.g. sqlite+aiosqlite:///data/bot.sqlite3")
    parser.add_argument("--min-score", type=int, default=None)
    args = parser.parse_args()
    settings = load_settings()
    database = Database(args.db or settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    try:
        params = {}
        if args.min_score is not None:
            params["min_score"] = args.min_score
        result = await BacktestEngine(database, settings).run(
            strategy_key=args.strategy,
            symbol=args.symbol.upper(),
            timeframe=args.timeframe,
            days=args.days,
            params=params,
        )
        output = {
            "status": result.get("status", "ok"),
            "message": result.get("message"),
            "period_start": result.get("period_start"),
            "period_end": result.get("period_end"),
            "candle_count": result.get("candle_count"),
            "snapshot_points": result.get("snapshot_points"),
            "metrics": result["metrics"],
        }
        print(json.dumps(output, indent=2, default=str, ensure_ascii=False))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(_main())
