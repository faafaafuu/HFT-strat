from datetime import datetime, timedelta

import pytest

from app.config import Settings
from app.data.database import Database
from app.data.repositories import HistoricalDataRepository
from app.optimization.optimizer import HyperOptimizer, _timeframe_list
from app.optimization.search_space import CHANNEL_SEARCH_SPACE, grid, search_space_for

START = datetime(2026, 1, 1)


def test_timeframe_list_parses_and_dedupes() -> None:
    assert _timeframe_list("4h") == ["4h"]
    assert _timeframe_list("15m, 1h ,4h") == ["15m", "1h", "4h"]
    assert _timeframe_list("1h,1h,4h") == ["1h", "4h"]
    assert _timeframe_list(["1h", "4h"]) == ["1h", "4h"]
    assert _timeframe_list("") == ["1m"]


def test_search_space_is_selected_per_strategy() -> None:
    assert search_space_for("channel_4_touch") is CHANNEL_SEARCH_SPACE
    assert "min_density_usd" in search_space_for("density_strategy")
    # Unknown strategies fall back to the generic simulator-level space.
    assert "stop_loss_pct" in search_space_for("trend_pullback_scalper")


def test_grid_is_deterministic_for_a_seed() -> None:
    assert grid(space=CHANNEL_SEARCH_SPACE, limit=20) == grid(space=CHANNEL_SEARCH_SPACE, limit=20)
    assert grid(space=CHANNEL_SEARCH_SPACE, limit=20, seed=1) != grid(
        space=CHANNEL_SEARCH_SPACE, limit=20, seed=2
    )


def _rows(timeframe: str, count: int, minutes: int) -> list[dict]:
    rows = []
    price = 100.0
    for index in range(count):
        price *= 1.0005 if index % 40 < 20 else 0.9995
        rows.append(
            {
                "exchange": "bybit",
                "symbol": "TESTUSDT",
                "timeframe": timeframe,
                "open_time": START + timedelta(minutes=index * minutes),
                "open": price,
                "high": price * 1.002,
                "low": price * 0.998,
                "close": price,
                "volume": 10.0,
                "turnover": 1000.0,
            }
        )
    return rows


async def _database() -> Database:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init()
    return database


@pytest.mark.asyncio
async def test_sweep_covers_every_requested_timeframe() -> None:
    database = await _database()
    try:
        async with database.session() as session:
            repo = HistoricalDataRepository(session)
            await repo.upsert_candles(_rows("1h", 900, 60))
            await repo.upsert_candles(_rows("4h", 900, 240))
        result = await HyperOptimizer(database, Settings()).run(
            strategy_key="channel_4_touch",
            symbol="TESTUSDT",
            timeframe="1h,4h",
            days=3650,
            limit=2,
        )
    finally:
        await database.close()

    assert result["timeframes"] == ["1h", "4h"]
    assert set(result["by_timeframe"]) == {"1h", "4h"}
    assert all(row["timeframe"] in {"1h", "4h"} for row in result["results"])


@pytest.mark.asyncio
async def test_missing_timeframe_does_not_abort_the_sweep() -> None:
    """One timeframe without candles must not cost the results of the others."""
    database = await _database()
    try:
        async with database.session() as session:
            await HistoricalDataRepository(session).upsert_candles(_rows("4h", 900, 240))
        result = await HyperOptimizer(database, Settings()).run(
            strategy_key="channel_4_touch",
            symbol="TESTUSDT",
            timeframe="1h,4h",
            days=3650,
            limit=2,
        )
    finally:
        await database.close()

    assert result["by_timeframe"]["1h"]["reason"] == "no_historical_candles"
    assert result["by_timeframe"]["4h"]["combinations"] == 2
    assert result["results"]


@pytest.mark.asyncio
async def test_single_missing_timeframe_still_reports_a_reason() -> None:
    database = await _database()
    try:
        result = await HyperOptimizer(database, Settings()).run(
            strategy_key="channel_4_touch",
            symbol="TESTUSDT",
            timeframe="4h",
            days=30,
            limit=2,
        )
    finally:
        await database.close()

    assert result["reason"] == "no_historical_candles"
    assert result["best"] is None
