from datetime import datetime, timedelta

import pytest

from app.backtesting.engine import BacktestEngine
from app.config import Settings
from app.exchanges.base import OrderbookEvent
from app.market.density_tracker import DensityTracker
from app.market.features import FeatureSnapshot
from app.strategies.density_strategy import DensityStrategy


def _snapshot(**overrides):
    data = {
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timestamp": datetime(2026, 1, 1),
        "price": 100.0,
        "price_change_5m_pct": 0.2,
        "volume_1m_usd": 300_000,
        "volume_5m_usd": 1_500_000,
        "avg_volume_5m_usd": 750_000,
        "volume_spike_ratio": 2.0,
        "oi": 10_000,
        "oi_change_5m_pct": 0.5,
        "oi_change_15m_pct": 1.0,
        "funding_rate_pct": 0.01,
        "spread_pct": 0.01,
        "bid_depth_1pct": 1_000_000,
        "ask_depth_1pct": 1_000_000,
        "swept_low_30m": None,
        "swept_high_30m": None,
        "returned_after_low_sweep": False,
        "returned_after_high_sweep": False,
        "trend_context": {"trend_alignment_score": 1.0},
    }
    data.update(overrides)
    return FeatureSnapshot(**data)


def test_density_tracker_emits_holding_event() -> None:
    tracker = DensityTracker(min_density_usd=500_000, max_distance_pct=1.0)
    start = datetime(2026, 1, 1)
    first = OrderbookEvent(
        exchange="bybit",
        symbol="BTCUSDT",
        timestamp=start,
        bids=[(99.8, 6_000)],
        asks=[(100.2, 100)],
    )
    second = OrderbookEvent(
        exchange="bybit",
        symbol="BTCUSDT",
        timestamp=start + timedelta(seconds=11),
        bids=[(99.8, 6_000)],
        asks=[(100.2, 100)],
    )

    tracker.update(first, mid_price=100)
    tracker.update(second, mid_price=100)

    events = tracker.drain_events()
    assert [event.event_type for event in events] == ["appeared", "holding"]
    assert events[-1].side == "bid"
    assert events[-1].size_usd >= 500_000


def test_density_strategy_generates_bounce_signal() -> None:
    settings = Settings()
    strategy = DensityStrategy(settings.density_strategy)
    signal = strategy.generate_signal(
        _snapshot(
            density_event={
                "side": "bid",
                "price": 99.8,
                "size_usd": 650_000,
                "distance_pct": 0.2,
                "lifetime_sec": 15,
                "event_type": "holding",
                "absorption_score": 0.0,
                "spoof_score": 0.0,
            }
        ),
        config={
            "min_density_usd": 500_000,
            "max_distance_pct": 0.35,
            "min_lifetime_sec": 10,
            "require_absorption": False,
            "require_trend_alignment": True,
        },
    )

    assert signal is not None
    assert signal.strategy_key == "density_strategy"
    assert signal.direction == "LONG"
    assert signal.market_context["density_setup"] == "density_bounce"


@pytest.mark.asyncio
async def test_density_backtest_requires_density_history() -> None:
    from datetime import datetime, timedelta

    from app.data.database import Database
    from app.data.repositories import HistoricalDataRepository

    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init()
    try:
        start = datetime(2026, 1, 1)
        async with database.session() as session:
            await HistoricalDataRepository(session).upsert_candles(
                [
                    {
                        "exchange": "bybit",
                        "symbol": "BTCUSDT",
                        "timeframe": "1m",
                        "open_time": start + timedelta(minutes=index),
                        "open": 100.0,
                        "high": 100.1,
                        "low": 99.9,
                        "close": 100.0,
                        "volume": 10.0,
                        "turnover": 1000.0,
                    }
                    for index in range(200)
                ]
            )
        result = await BacktestEngine(database, Settings()).run(
            strategy_key="density_strategy",
            symbol="BTCUSDT",
            persist=False,
        )
    finally:
        await database.close()

    assert result["status"] == "insufficient_density_history"
    assert "L2/orderbook" in result["message"]
