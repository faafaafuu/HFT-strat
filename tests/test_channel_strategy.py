import math
from datetime import datetime, timedelta

import pytest

from app.backtesting.engine import MIN_BACKTEST_CANDLES, BacktestEngine
from app.config import ChannelStrategyConfig, Settings
from app.data.database import Database
from app.data.repositories import HistoricalDataRepository
from app.market.features import CandleBar, FeatureSnapshot
from app.optimization.search_space import CHANNEL_SEARCH_SPACE, grid
from app.strategies.channel_touch import ChannelTouchStrategy
from app.utils.time import timeframe_minutes

START = datetime(2026, 1, 1)


def _snapshot(candles: list[CandleBar], **overrides) -> FeatureSnapshot:
    data = {
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timestamp": candles[-1].open_time if candles else START,
        "price": candles[-1].close if candles else 100.0,
        "price_change_5m_pct": 0.0,
        "volume_1m_usd": 100_000,
        "volume_5m_usd": 500_000,
        "avg_volume_5m_usd": 500_000,
        "volume_spike_ratio": 1.0,
        "oi": None,
        "oi_change_5m_pct": None,
        "oi_change_15m_pct": None,
        "funding_rate_pct": None,
        "spread_pct": 0.01,
        "bid_depth_1pct": None,
        "ask_depth_1pct": None,
        "swept_low_30m": None,
        "swept_high_30m": None,
        "returned_after_low_sweep": False,
        "returned_after_high_sweep": False,
        "candles": tuple(candles),
    }
    data.update(overrides)
    return FeatureSnapshot(**data)


def _bar(index: int, high: float, low: float, close: float | None = None) -> CandleBar:
    close = (high + low) / 2 if close is None else close
    return CandleBar(
        open_time=START + timedelta(minutes=index),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=close,
        volume=10.0,
    )


def _rising_channel_bars(
    *,
    slope: float = 0.02,
    upper: float = 110.0,
    lower: float = 100.0,
    touch_gap_pct: float = 0.0,
) -> list[CandleBar]:
    """Points 1 and 3 tag the upper line, point 2 the lower; the last bar is touch 4.

    Layout: point 1 at bar 20, point 2 at bar 40, point 3 at bar 60, touch 4 at bar 90.
    """
    bars: list[CandleBar] = []
    for index in range(120):
        top = upper + slope * index
        bottom = lower + slope * index
        middle = (top + bottom) / 2
        high, low, close = middle + 1.0, middle - 1.0, middle
        if index in (20, 60):
            high, low, close = top, middle - 1.0, middle
        elif index == 40:
            high, low, close = middle + 1.0, bottom, middle
        elif index == 90:
            # Wick into the lower line and close just above it: the rejection bar.
            low = bottom * (1 + touch_gap_pct / 100)
            close = low * 1.003
            high = low * 1.004
        bars.append(_bar(index, high, low, close))
    return bars[:91]


def _strategy(**overrides) -> ChannelTouchStrategy:
    settings = {
        "pivot_lookback": 3,
        "min_bars_between_points": 5,
        "max_bars_wait_touch": 60,
        "touch_tolerance_pct": 0.1,
        "breakout_buffer_pct": 0.1,
        "stop_pct": 1.0,
        "max_stop_pct": 1.5,
        "take_pct": 4.0,
        "min_rr": 2.0,
    }
    settings.update(overrides)
    return ChannelTouchStrategy(ChannelStrategyConfig(**settings))


def test_fourth_touch_of_lower_boundary_is_a_long() -> None:
    signal = _strategy().generate_signal(_snapshot(_rising_channel_bars()))

    assert signal is not None
    assert signal.direction == "LONG"
    assert signal.strategy_key == "channel_4_touch"
    channel = signal.market_context["channel"]
    assert channel["anchor_side"] == "upper"
    assert channel["touch_side"] == "lower"
    assert channel["point_bars_ago"] == [70, 50, 30]
    assert signal.suggested_take_pct / signal.suggested_stop_pct >= 2.0


def test_mirrored_channel_gives_a_short() -> None:
    """Points 1 and 3 on the lower line make the 4th touch an upper-boundary short."""
    bars: list[CandleBar] = []
    slope, upper, lower = -0.02, 110.0, 100.0
    for index in range(91):
        top = upper + slope * index
        bottom = lower + slope * index
        middle = (top + bottom) / 2
        high, low, close = middle + 1.0, middle - 1.0, middle
        if index in (20, 60):
            high, low, close = middle + 1.0, bottom, middle
        elif index == 40:
            high, low, close = top, middle - 1.0, middle
        elif index == 90:
            high = top
            close = high * 0.997
            low = high * 0.996
        bars.append(_bar(index, high, low, close))

    signal = _strategy().generate_signal(_snapshot(bars))

    assert signal is not None
    assert signal.direction == "SHORT"
    assert signal.market_context["channel"]["touch_side"] == "upper"


def test_close_beyond_boundary_kills_the_channel() -> None:
    """A decisive close outside means the channel broke, not that it was touched."""
    bars = _rising_channel_bars()
    broken = list(bars)
    # Bar 75 closes well below the lower line, between point 3 and the touch.
    reference = broken[75]
    broken[75] = _bar(75, reference.high, reference.low * 0.9, reference.low * 0.9)

    assert _strategy().generate_signal(_snapshot(broken)) is None


def test_touch_that_closes_outside_is_not_a_touch() -> None:
    """Only a wick counts: a close through the line is a breakout."""
    bars = _rising_channel_bars()
    entry = bars[-1]
    bars[-1] = _bar(90, entry.high, entry.low * 0.98, entry.low * 0.985)

    assert _strategy().generate_signal(_snapshot(bars)) is None


def test_wick_short_of_the_line_is_not_a_touch() -> None:
    far = _rising_channel_bars(touch_gap_pct=1.0)

    assert _strategy().generate_signal(_snapshot(far)) is None


def test_only_the_first_touch_after_point_three_signals() -> None:
    """A later re-test of the same boundary is no longer the 4th touch."""
    bars = _rising_channel_bars()
    lower_at_75 = 100.0 + 0.02 * 75
    middle = lower_at_75 + 5.0
    bars[75] = _bar(75, middle + 1.0, lower_at_75, middle)

    assert _strategy().generate_signal(_snapshot(bars)) is None


def test_stale_channel_stops_producing_signals() -> None:
    assert _strategy(max_bars_wait_touch=20).generate_signal(_snapshot(_rising_channel_bars())) is None


def test_setup_below_min_rr_is_skipped() -> None:
    assert _strategy(min_rr=99.0).generate_signal(_snapshot(_rising_channel_bars())) is None


def test_stop_wider_than_max_is_skipped() -> None:
    assert _strategy(stop_pct=2.0, max_stop_pct=1.5).generate_signal(
        _snapshot(_rising_channel_bars())
    ) is None


def test_take_profit_is_capped_by_the_opposite_boundary() -> None:
    """Target is the fixed take or the far boundary - whichever is closer."""
    signal = _strategy(take_pct=50.0, min_rr=1.0).generate_signal(
        _snapshot(_rising_channel_bars())
    )

    assert signal is not None
    channel = signal.market_context["channel"]
    assert channel["target_is_boundary"] is True
    entry = signal.entry_reference
    expected = (channel["upper"] - entry) / entry * 100
    assert signal.suggested_take_pct == pytest.approx(expected)


def test_stop_sits_beyond_the_touch_wick() -> None:
    signal = _strategy(stop_pct=0.1, min_rr=1.0).generate_signal(
        _snapshot(_rising_channel_bars())
    )

    assert signal is not None
    entry = signal.entry_reference
    stop_price = entry * (1 - signal.suggested_stop_pct / 100)
    assert stop_price < signal.invalidation_level


def test_no_candles_means_no_signal() -> None:
    assert _strategy().generate_signal(_snapshot([])) is None


def test_flat_price_has_no_channel() -> None:
    bars = [_bar(index, 101.0, 99.0, 100.0) for index in range(120)]

    assert _strategy().generate_signal(_snapshot(bars)) is None


@pytest.mark.parametrize(
    ("timeframe", "minutes"),
    [("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60), ("3h", 180), ("1d", 1440), ("30", 30)],
)
def test_timeframe_minutes(timeframe: str, minutes: int) -> None:
    assert timeframe_minutes(timeframe) == minutes


def test_timeframe_minutes_rejects_junk() -> None:
    with pytest.raises(ValueError):
        timeframe_minutes("weekly")


def test_search_space_sampling_varies_every_parameter() -> None:
    """Truncating the product would pin slow-varying keys to one value."""
    rows = grid(space=CHANNEL_SEARCH_SPACE, limit=50)

    assert len(rows) == 50
    for key, values in CHANNEL_SEARCH_SPACE.items():
        if len(values) > 1:
            assert len({row[key] for row in rows}) > 1, key


def test_small_space_is_returned_whole() -> None:
    rows = grid(space={"a": [1, 2], "b": [3, 4]}, limit=100)

    assert sorted((row["a"], row["b"]) for row in rows) == [(1, 3), (1, 4), (2, 3), (2, 4)]


async def _database() -> Database:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init()
    return database


def _channel_candle_rows(count: int = 900) -> list[dict]:
    """Price oscillating inside a flat channel, tagging each boundary in turn.

    Closes follow a cosine, so a turning bar closes right next to the boundary its
    wick tagged - which is what makes the structural stop tight enough to trade.
    """
    middle = 100.0
    amplitude = 2.5
    wick = 0.1
    rows: list[dict] = []
    for index in range(count):
        close = middle + amplitude * math.cos(2 * math.pi * index / 40)
        rows.append(
            {
                "exchange": "bybit",
                "symbol": "TESTUSDT",
                "timeframe": "1m",
                "open_time": START + timedelta(minutes=index),
                "open": close,
                "high": close + wick,
                "low": close - wick,
                "close": close,
                "volume": 10.0,
                "turnover": 10.0 * close,
            }
        )
    return rows


@pytest.mark.asyncio
async def test_engine_feeds_candles_to_the_strategy() -> None:
    """The channel strategy is unusable unless the engine hands it an OHLC window."""
    database = await _database()
    try:
        async with database.session() as session:
            await HistoricalDataRepository(session).upsert_candles(_channel_candle_rows())
        result = await BacktestEngine(database, Settings()).run(
            strategy_key="channel_4_touch",
            symbol="TESTUSDT",
            days=30,
            params={"min_score": 1},
            persist=False,
        )
    finally:
        await database.close()

    assert result["status"] == "ok"
    assert result["metrics"]["total_trades"] >= 1


@pytest.mark.asyncio
async def test_engine_reports_history_shorter_than_the_strategy_lookback() -> None:
    database = await _database()
    try:
        async with database.session() as session:
            await HistoricalDataRepository(session).upsert_candles(
                _channel_candle_rows()[: MIN_BACKTEST_CANDLES + 10]
            )
        result = await BacktestEngine(database, Settings()).run(
            strategy_key="channel_4_touch",
            symbol="TESTUSDT",
            persist=False,
        )
    finally:
        await database.close()

    assert result["status"] == "insufficient_candles"
    assert "channel_4_touch" in result["message"]


@pytest.mark.asyncio
async def test_backtest_params_reach_the_strategy() -> None:
    """Hyperopt is pointless if search-space values never leave the engine."""
    database = await _database()
    try:
        async with database.session() as session:
            await HistoricalDataRepository(session).upsert_candles(_channel_candle_rows())
        engine = BacktestEngine(database, Settings())
        permissive = await engine.run(
            strategy_key="channel_4_touch",
            symbol="TESTUSDT",
            params={"min_score": 1},
            persist=False,
        )
        strict = await engine.run(
            strategy_key="channel_4_touch",
            symbol="TESTUSDT",
            params={"min_score": 1, "min_rr": 99.0},
            persist=False,
        )
    finally:
        await database.close()

    assert permissive["metrics"]["total_trades"] >= 1
    assert strict["metrics"]["total_trades"] == 0
