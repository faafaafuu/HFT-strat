from dataclasses import dataclass
from datetime import datetime, timedelta

from app.backtesting.simulator import simulate_exit


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float


def test_simulate_long_take_profit() -> None:
    start = datetime(2026, 1, 1)
    trade = simulate_exit(
        direction="LONG",
        entry_time=start,
        entry_price=100,
        future_candles=[Candle(start + timedelta(minutes=1), 100, 102, 99.8, 101)],
        stop_pct=0.5,
        take_pct=1.0,
        max_holding_candles=10,
        position_size_usd=1000,
        taker_fee_pct=0.055,
        slippage_pct=0.0,
    )

    assert trade is not None
    assert trade.status == "TP"
    assert trade.pnl_usd > 0


def test_simulate_short_stop_loss() -> None:
    start = datetime(2026, 1, 1)
    trade = simulate_exit(
        direction="SHORT",
        entry_time=start,
        entry_price=100,
        future_candles=[Candle(start + timedelta(minutes=1), 100, 101, 99, 100.8)],
        stop_pct=0.5,
        take_pct=1.0,
        max_holding_candles=10,
        position_size_usd=1000,
        taker_fee_pct=0.055,
        slippage_pct=0.0,
    )

    assert trade is not None
    assert trade.status == "SL"
    assert trade.pnl_usd < 0
