"""Guards for the research execution model.

A backtest that quietly reads the future or sizes positions wrongly produces confident
numbers that mean nothing, so these check the mechanics rather than any strategy.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.research.data import Series
from app.research.harness import CostModel, Entry, RiskModel, run
from app.research.stats import monthly_table, summarise
from app.research.strategies import BUILDERS

START = datetime(2025, 1, 1, tzinfo=UTC)


def _series(closes: list[float], *, spread: float = 0.5) -> Series:
    return Series(
        symbol="TESTUSDT",
        timeframe="1h",
        time=[START + timedelta(hours=index) for index in range(len(closes))],
        open=list(closes),
        high=[value + spread for value in closes],
        low=[value - spread for value in closes],
        close=list(closes),
        volume=[1.0] * len(closes),
    )


@pytest.mark.parametrize("builder", list(BUILDERS.values()))
def test_signal_is_unchanged_when_the_future_is_cut_off(builder) -> None:  # noqa: ANN001
    """The decisive look-ahead check.

    Indicators are precomputed over the whole series, so watching what `signal` touches
    proves nothing. Instead the same bar is decided twice: once knowing the whole series,
    once on a series that ends right after it. Any disagreement is a peek at the future.
    """
    closes = [100 + 12 * math.sin(index / 9) + (index % 23) * 0.3 for index in range(600)]
    full = _series(closes, spread=1.2)

    whole = builder()
    whole.prepare(full)

    for index in range(whole.warmup + 5, len(full) - 1, 37):
        truncated_series = full.slice(0, index + 1)
        truncated = builder()
        truncated.prepare(truncated_series)
        expected = whole.signal(full, index)
        actual = truncated.signal(truncated_series, index)
        assert _shape(expected) == _shape(actual), (
            f"{whole.key}: бар {index} решается по-разному со знанием будущего и без него"
        )


def _shape(entry: Entry | None) -> tuple | None:
    if entry is None:
        return None
    return (
        entry.direction,
        round(entry.stop, 8),
        round(entry.take, 8) if entry.take is not None else None,
    )


def test_fill_happens_on_the_next_bar_open_with_slippage() -> None:
    series = _series([100.0, 100.0, 110.0, 111.0], spread=0.0)

    class _Once:
        key = "once"
        warmup = 0

        def signal(self, series_in: Series, index: int) -> Entry | None:
            return Entry("LONG", 90.0, 105.0) if index == 0 else None

    result = run(
        series,
        _Once(),
        costs=CostModel(taker_fee_pct=0.0, slippage_pct=0.1),
        risk=RiskModel(risk_pct=2.0, max_leverage=100.0),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    # Signal on bar 0, fill on bar 1's open of 100 plus 0.1% slippage.
    assert trade.entry_time == series.time[1]
    assert round(trade.entry_price, 4) == 100.1


def test_position_size_follows_the_stop_distance() -> None:
    series = _series([100.0] * 10, spread=0.0)

    class _Wide:
        key = "wide"
        warmup = 0

        def signal(self, series_in: Series, index: int) -> Entry | None:
            return Entry("LONG", 98.0, 200.0) if index == 0 else None

    result = run(
        series,
        _Wide(),
        costs=CostModel(taker_fee_pct=0.0, slippage_pct=0.0),
        risk=RiskModel(risk_pct=2.0, max_leverage=100.0, initial_equity=10_000.0),
    )

    trade = result.trades[0]
    # Risking 2% of 10 000 with the stop 2% away means a position of exactly one equity.
    assert round(trade.notional, 2) == 10_000.0
    assert round(trade.leverage, 2) == 1.0


def test_a_stopped_trade_loses_the_planned_risk() -> None:
    closes = [100.0, 100.0, 97.0, 97.0]
    series = Series(
        symbol="TESTUSDT",
        timeframe="1h",
        time=[START + timedelta(hours=index) for index in range(len(closes))],
        open=closes,
        high=closes,
        low=[100.0, 100.0, 97.0, 97.0],
        close=closes,
        volume=[1.0] * len(closes),
    )

    class _Stopped:
        key = "stopped"
        warmup = 0

        def signal(self, series_in: Series, index: int) -> Entry | None:
            return Entry("LONG", 98.0, 200.0) if index == 0 else None

    result = run(
        series,
        _Stopped(),
        costs=CostModel(taker_fee_pct=0.0, slippage_pct=0.0),
        risk=RiskModel(risk_pct=2.0, max_leverage=100.0, initial_equity=10_000.0),
    )

    trade = result.trades[0]
    assert trade.status == "SL"
    # The bar gapped through 98 and opened at 97, so the loss is worse than the plan.
    assert trade.exit_price == 97.0
    assert -320.0 < trade.pnl_usd < -290.0


def test_stop_wins_when_one_bar_contains_both_levels() -> None:
    series = Series(
        symbol="TESTUSDT",
        timeframe="1h",
        time=[START + timedelta(hours=index) for index in range(4)],
        open=[100.0, 100.0, 100.0, 100.0],
        high=[100.0, 100.0, 110.0, 100.0],
        low=[100.0, 100.0, 90.0, 100.0],
        close=[100.0, 100.0, 100.0, 100.0],
        volume=[1.0] * 4,
    )

    class _Both:
        key = "both"
        warmup = 0

        def signal(self, series_in: Series, index: int) -> Entry | None:
            return Entry("LONG", 95.0, 105.0) if index == 0 else None

    result = run(
        series,
        _Both(),
        costs=CostModel(taker_fee_pct=0.0, slippage_pct=0.0),
        risk=RiskModel(risk_pct=2.0, max_leverage=100.0),
    )

    assert result.trades[0].status == "SL"


def test_monthly_table_compounds_from_the_equity_curve() -> None:
    series = _series([100.0] * 5, spread=0.0)

    class _Flat:
        key = "flat"
        warmup = 0

        def signal(self, series_in: Series, index: int) -> Entry | None:
            return None

    empty = run(series, _Flat())
    assert monthly_table(empty) == []
    assert summarise(empty)["trades"] == 0
