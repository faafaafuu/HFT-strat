"""Candle loading for research runs.

Reads the sqlite file directly and read-only: a research sweep must never take a write
lock the live bot is waiting on, and it must not depend on the app's runtime settings.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DB = Path("data/bot.sqlite3")


@dataclass(frozen=True)
class Series:
    """Column-oriented candles: the inner loops index these millions of times."""

    symbol: str
    timeframe: str
    time: list[datetime]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.time)

    def slice(self, start: int, end: int | None = None) -> Series:
        end = len(self) if end is None else end
        return Series(
            symbol=self.symbol,
            timeframe=self.timeframe,
            time=self.time[start:end],
            open=self.open[start:end],
            high=self.high[start:end],
            low=self.low[start:end],
            close=self.close[start:end],
            volume=self.volume[start:end],
        )

    def index_at(self, moment: datetime) -> int:
        """First bar at or after `moment`; len(self) when the series ends earlier."""
        for index, stamp in enumerate(self.time):
            if stamp >= moment:
                return index
        return len(self)


def load_series(
    symbol: str,
    timeframe: str,
    *,
    db_path: Path | str = DEFAULT_DB,
    exchange: str = "bybit",
) -> Series:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            select open_time, open, high, low, close, volume
            from historical_candles
            where exchange = ? and symbol = ? and timeframe = ?
            order by open_time
            """,
            (exchange, symbol, timeframe),
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise ValueError(f"Нет свечей {symbol} {timeframe} в {db_path}")
    return Series(
        symbol=symbol,
        timeframe=timeframe,
        time=[_parse(row[0]) for row in rows],
        open=[float(row[1]) for row in rows],
        high=[float(row[2]) for row in rows],
        low=[float(row[3]) for row in rows],
        close=[float(row[4]) for row in rows],
        volume=[float(row[5]) for row in rows],
    )


def available(db_path: Path | str = DEFAULT_DB) -> list[tuple[str, str, int]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [
            (str(row[0]), str(row[1]), int(row[2]))
            for row in connection.execute(
                "select symbol, timeframe, count(*) from historical_candles "
                "group by symbol, timeframe order by symbol, count(*) desc"
            )
        ]
    finally:
        connection.close()


def _parse(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    stamp = datetime.fromisoformat(str(value))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
