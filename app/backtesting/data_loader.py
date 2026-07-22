from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.data.database import Database
from app.data.repositories import HistoricalDataRepository
from app.exchanges.bybit_client import BybitClient
from app.jobs.cancellation import CancellationToken
from app.utils.time import timeframe_minutes, utc_now

# Bybit kline only serves these intervals; anything else has to be resampled locally.
BYBIT_INTERVALS: dict[int, str] = {
    1: "1",
    3: "3",
    5: "5",
    15: "15",
    30: "30",
    60: "60",
    120: "120",
    240: "240",
    360: "360",
    720: "720",
    1440: "D",
    10080: "W",
}


def bybit_interval(timeframe: str) -> str:
    minutes = timeframe_minutes(timeframe)
    interval = BYBIT_INTERVALS.get(minutes)
    if interval is None:
        supported = ", ".join(str(value) for value in sorted(BYBIT_INTERVALS))
        raise ValueError(
            f"Bybit has no {timeframe} kline interval. Supported minutes: {supported}."
        )
    return interval


async def download_bybit_history(
    database: Database,
    *,
    symbol: str,
    timeframe: str,
    days: int,
    cancellation: CancellationToken | None = None,
) -> int:
    rows: list[dict[str, Any]] = []
    interval = bybit_interval(timeframe)
    interval_minutes = timeframe_minutes(timeframe)
    end = utc_now()
    start = end - timedelta(days=days)
    async with BybitClient(testnet=False) as client:
        cursor = start
        while cursor < end:
            # Between chunks, so a cancelled download stops without a half-read request.
            if cancellation is not None:
                await cancellation.raise_if_cancelled()
            chunk_end = min(end, cursor + timedelta(minutes=interval_minutes * 1000))
            raw = await client.kline(
                symbol,
                interval=interval,
                limit=1000,
                start=int(cursor.timestamp() * 1000),
                end=int(chunk_end.timestamp() * 1000),
            )
            rows.extend(_candle_rows(symbol, timeframe, raw))
            cursor = chunk_end + timedelta(milliseconds=1)
    rows.sort(key=lambda row: row["open_time"])
    deduped = {(row["exchange"], row["symbol"], row["timeframe"], row["open_time"]): row for row in rows}
    async with database.session() as session:
        return await HistoricalDataRepository(session).upsert_candles(list(deduped.values()))


def _candle_rows(symbol: str, timeframe: str, raw: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for item in raw:
        open_time = datetime.fromtimestamp(int(item[0]) / 1000, tz=UTC)
        rows.append(
            {
                "exchange": "bybit",
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": open_time,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "turnover": float(item[6]) if len(item) > 6 else 0.0,
                "created_at": utc_now(),
            }
        )
    return rows
