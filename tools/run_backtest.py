from __future__ import annotations

import argparse
import asyncio
import json

from app.backtesting.engine import BacktestEngine
from app.config import load_settings
from app.data.database import Database


def _parse_params(pairs: list[str]) -> dict[str, object]:
    params: dict[str, object] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--param expects KEY=VALUE, got: {pair}")
        key, raw = pair.split("=", 1)
        params[key.strip()] = _coerce(raw.strip())
    return params


def _coerce(raw: str) -> object:
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--db", default=None, help="Override database URL, e.g. sqlite+aiosqlite:///data/bot.sqlite3")
    parser.add_argument("--min-score", type=int, default=None)
    parser.add_argument(
        "-p",
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Any strategy or exit-rule parameter, repeatable. "
            "Examples: -p stop_pct=0.5 -p trailing_enabled=true -p trailing_distance_pct=2"
        ),
    )
    args = parser.parse_args()
    settings = load_settings()
    database = Database(args.db or settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    try:
        params = _parse_params(args.param)
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
