from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.config import DatabaseConfig, Settings, StorageConfig
from app.data.database import Database
from app.data.repositories import HistoricalDataRepository, HyperoptCacheRepository
from app.jobs.models import JobCancelled
from app.optimization.optimizer import HyperOptimizer, _cache_key

START = datetime(2026, 1, 1)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database=DatabaseConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'bot.sqlite3'}"),
        storage=StorageConfig(
            data_dir=str(tmp_path / "data"),
            logs_dir=str(tmp_path / "logs"),
            backups_dir=str(tmp_path / "backups"),
        ),
    )


async def _seed_candles(database: Database, count: int = 400) -> None:
    rows = []
    price = 100.0
    for index in range(count):
        price += 0.6 if index % 3 else -0.4
        rows.append(
            {
                "exchange": "bybit",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "open_time": START + timedelta(hours=index),
                "open": price,
                "high": price * 1.004,
                "low": price * 0.996,
                "close": price * 1.001,
                "volume": 1000.0,
                "turnover": 1000.0 * price,
            }
        )
    async with database.session() as session:
        await HistoricalDataRepository(session).upsert_candles(rows)


def test_cache_key_ignores_parameter_order_but_tracks_values() -> None:
    common = {
        "strategy_key": "channel_4_touch",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "period_start": START,
        "period_end": START + timedelta(days=10),
        "split_at": 100,
    }

    same = _cache_key(params={"stop_pct": 1.0, "take_pct": 4.0}, **common)
    reordered = _cache_key(params={"take_pct": 4.0, "stop_pct": 1.0}, **common)
    different = _cache_key(params={"stop_pct": 1.5, "take_pct": 4.0}, **common)

    assert same == reordered
    assert same != different


def test_cache_key_changes_with_the_candle_window() -> None:
    common = {
        "strategy_key": "channel_4_touch",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "params": {"stop_pct": 1.0},
        "period_start": START,
        "split_at": 100,
    }

    first = _cache_key(period_end=START + timedelta(days=10), **common)
    later = _cache_key(period_end=START + timedelta(days=11), **common)

    assert first != later


@pytest.mark.asyncio
async def test_second_sweep_reuses_stored_evaluations(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    await _seed_candles(database)

    optimizer = HyperOptimizer(database, settings)
    first = await optimizer.run(
        strategy_key="channel_4_touch", symbol="BTCUSDT", timeframe="1h", days=30, limit=4
    )
    second = await optimizer.run(
        strategy_key="channel_4_touch", symbol="BTCUSDT", timeframe="1h", days=30, limit=4
    )

    async with database.session() as session:
        stats = await HyperoptCacheRepository(session).stats()
    await database.close()

    assert first["computed"] == 4
    assert first["from_cache"] == 0
    assert second["computed"] == 0
    assert second["from_cache"] == 4
    # Reuse must not change the answer.
    assert [row["objective"] for row in second["results"]] == [
        row["objective"] for row in first["results"]
    ]
    assert stats["total"] == 4
    assert stats["reused"] == 4


@pytest.mark.asyncio
async def test_clearing_the_cache_forces_recomputation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    await _seed_candles(database)

    optimizer = HyperOptimizer(database, settings)
    await optimizer.run(
        strategy_key="channel_4_touch", symbol="BTCUSDT", timeframe="1h", days=30, limit=3
    )
    async with database.session() as session:
        removed = await HyperoptCacheRepository(session).clear()
    again = await optimizer.run(
        strategy_key="channel_4_touch", symbol="BTCUSDT", timeframe="1h", days=30, limit=3
    )
    await database.close()

    assert removed == 3
    assert again["computed"] == 3


@pytest.mark.asyncio
async def test_cancelled_sweep_keeps_what_it_already_computed(tmp_path: Path) -> None:
    """Cancelling must not throw away minutes of work: the next sweep resumes from cache."""
    settings = _settings(tmp_path)
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.init()
    await _seed_candles(database)

    optimizer = HyperOptimizer(database, settings)
    token = _StopAfter(2)

    with pytest.raises(JobCancelled):
        await optimizer.run(
            strategy_key="channel_4_touch",
            symbol="BTCUSDT",
            timeframe="1h",
            days=30,
            limit=8,
            cancellation=token,
        )

    async with database.session() as session:
        stats = await HyperoptCacheRepository(session).stats()
    await database.close()

    stored = sum(row["combinations"] for row in stats["rows"])
    assert stored == 2, "должны сохраниться ровно те комбинации, что успели посчитаться"


class _StopAfter:
    """Cancellation that fires after a fixed number of checkpoints."""

    def __init__(self, allowed: int) -> None:
        self.allowed = allowed
        self.seen = 0

    async def raise_if_cancelled(self) -> None:
        if self.seen >= self.allowed:
            raise JobCancelled("отменена")
        self.seen += 1
