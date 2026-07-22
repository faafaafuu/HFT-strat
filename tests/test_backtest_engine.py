from datetime import datetime, timedelta

import pytest

from app.backtesting.engine import MIN_BACKTEST_CANDLES, BacktestEngine
from app.config import Settings
from app.data.database import Database
from app.data.models import MarketSnapshotModel
from app.data.repositories import HistoricalDataRepository
from app.strategies.base import clamp_score, scale_points, spread_bonus

START = datetime(2026, 1, 1)


async def _database() -> Database:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init()
    return database


def _candle_rows(count: int = 400, spike_at: int = 200) -> list[dict]:
    rows = []
    price = 100.0
    for index in range(count):
        volume = 10.0
        if spike_at <= index < spike_at + 5:
            price *= 1.0015
            volume = 50.0
        elif spike_at + 5 <= index < spike_at + 30:
            price *= 1.001
        open_price = price / 1.0015 if spike_at <= index < spike_at + 5 else price
        rows.append(
            {
                "exchange": "bybit",
                "symbol": "TESTUSDT",
                "timeframe": "1m",
                "open_time": START + timedelta(minutes=index),
                "open": open_price,
                "high": price * 1.0005,
                "low": min(open_price, price) * 0.9995,
                "close": price,
                "volume": volume,
                "turnover": volume * price,
            }
        )
    return rows


async def _seed_candles(database: Database, rows: list[dict]) -> None:
    async with database.session() as session:
        await HistoricalDataRepository(session).upsert_candles(rows)


async def _seed_snapshots(database: Database, count: int = 400) -> None:
    async with database.session() as session:
        for index in range(count):
            session.add(
                MarketSnapshotModel(
                    exchange="bybit",
                    symbol="TESTUSDT",
                    timestamp=START + timedelta(minutes=index),
                    price=100.0,
                    oi=1_000_000.0,
                    oi_change_5m=1.5,
                    oi_change_15m=3.0,
                    spread_pct=0.01,
                )
            )


@pytest.mark.asyncio
async def test_backtest_reports_no_candles() -> None:
    database = await _database()
    try:
        result = await BacktestEngine(database, Settings()).run(
            strategy_key="trend_pullback_scalper",
            symbol="TESTUSDT",
            persist=False,
        )
    finally:
        await database.close()

    assert result["status"] == "no_candles"
    assert "TESTUSDT" in result["message"]


@pytest.mark.asyncio
async def test_backtest_reports_insufficient_candles() -> None:
    database = await _database()
    try:
        await _seed_candles(database, _candle_rows(count=MIN_BACKTEST_CANDLES - 20, spike_at=999))
        result = await BacktestEngine(database, Settings()).run(
            strategy_key="trend_pullback_scalper",
            symbol="TESTUSDT",
            persist=False,
        )
    finally:
        await database.close()

    assert result["status"] == "insufficient_candles"


@pytest.mark.asyncio
async def test_backtest_window_is_anchored_to_last_candle() -> None:
    """History that ended long ago must still be backtestable."""
    database = await _database()
    try:
        await _seed_candles(database, _candle_rows())
        result = await BacktestEngine(database, Settings()).run(
            strategy_key="trend_pullback_scalper",
            symbol="TESTUSDT",
            days=30,
            persist=False,
        )
    finally:
        await database.close()

    assert result["status"] == "ok"
    assert result["candle_count"] == 400
    assert result["metrics"]["total_trades"] >= 1


@pytest.mark.asyncio
async def test_oi_strategy_requires_snapshot_history() -> None:
    database = await _database()
    try:
        await _seed_candles(database, _candle_rows())
        result = await BacktestEngine(database, Settings()).run(
            strategy_key="oi_pump_price_move",
            symbol="TESTUSDT",
            persist=False,
        )
    finally:
        await database.close()

    assert result["status"] == "no_oi_history"


@pytest.mark.asyncio
async def test_oi_strategy_trades_with_snapshot_history() -> None:
    database = await _database()
    try:
        await _seed_candles(database, _candle_rows())
        await _seed_snapshots(database)
        result = await BacktestEngine(database, Settings()).run(
            strategy_key="oi_pump_price_move",
            symbol="TESTUSDT",
            persist=False,
        )
    finally:
        await database.close()

    assert result["status"] == "ok"
    assert result["snapshot_points"] == 400
    assert result["metrics"]["total_trades"] >= 1


def test_scale_points_is_graded() -> None:
    assert scale_points(1.0, 1.0, 2.0) == 0.0
    assert scale_points(1.5, 1.0, 2.0) == 1.0
    assert scale_points(2.0, 1.0, 2.0) == 2.0
    assert scale_points(5.0, 1.0, 2.0) == 2.0
    assert scale_points(None, 1.0, 2.0) == 0.0


def test_spread_bonus_and_clamp() -> None:
    assert spread_bonus(None) == 0.0
    assert spread_bonus(0.01) == 0.5
    assert spread_bonus(0.04) == 0.25
    assert spread_bonus(0.2) == 0.0
    assert clamp_score(12.4) == 10
    assert clamp_score(-3) == 1
    assert clamp_score(7.4) == 7
