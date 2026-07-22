"""Cross-sectional strategies: bets on relative strength rather than on direction.

Every hypothesis so far was a bet that a particular symbol goes up or down, and those all
move together in crypto — which is why the portfolio's drawdown grew faster than its
return. Ranking symbols against each other and buying the strong while selling the weak
produces a book whose net exposure is small, so the correlated part of the move cancels.

Positions are opened at the open of the bar after the ranking bar and held to the next
rebalance unless a protective stop is hit first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.research.data import Series
from app.research.harness import CostModel, RiskModel, RunResult, Trade
from app.research.indicators import atr, ema


@dataclass(frozen=True)
class CrossSectionConfig:
    lookback: int = 168
    rebalance: int = 24
    longs: int = 3
    shorts: int = 3
    stop_atr_mult: float = 4.0
    atr_period: int = 24
    # Skip the most recent bars when ranking: short-term reversal pollutes momentum.
    skip: int = 0
    # Only hold the book while the market itself is not in free fall.
    regime_ema: int = 0
    min_symbols: int = 6
    # Reverse the ranking to bet on short-term reversal instead of momentum.
    reverse: bool = False


@dataclass
class _Prepared:
    series: Series
    atr: list[float | None]
    index_by_time: dict[datetime, int]


def run_cross_section(
    all_series: list[Series],
    config: CrossSectionConfig,
    *,
    costs: CostModel,
    risk: RiskModel,
    start: int = 0,
    end: int | None = None,
    regime_series: Series | None = None,
) -> RunResult:
    prepared = [
        _Prepared(series, atr(series, config.atr_period), {t: i for i, t in enumerate(series.time)})
        for series in all_series
    ]
    grid = _common_timeline(all_series)
    last = (len(grid) if end is None else min(end, len(grid))) - 1
    first = max(start, config.lookback + config.skip + config.atr_period + 1)
    regime = _regime_filter(regime_series, config)

    equity = risk.initial_equity
    trades: list[Trade] = []
    curve: list[tuple[datetime, float]] = []
    position_bar = first
    while position_bar < last:
        moment = grid[position_bar]
        ranked = _ranked(prepared, moment, config)
        if len(ranked) < config.min_symbols:
            position_bar += config.rebalance
            continue
        allowed = regime(moment) if regime else True
        picks: list[tuple[_Prepared, str]] = []
        if allowed:
            picks += [(item, "LONG") for item, _ in ranked[: config.longs]]
        picks += [(item, "SHORT") for item, _ in ranked[len(ranked) - config.shorts :]]

        exit_bar = min(position_bar + config.rebalance, last)
        realised = 0.0
        for item, direction in picks:
            trade = _hold(
                item,
                direction,
                moment,
                grid[exit_bar],
                equity=equity,
                config=config,
                costs=costs,
                risk=risk,
            )
            if trade is None:
                continue
            trades.append(trade)
            realised += trade.pnl_usd
        equity += realised
        curve.append((grid[exit_bar], equity))
        if equity <= 0:
            break
        position_bar = exit_bar

    return RunResult(
        symbol="КРОСС-СЕКЦИЯ",
        timeframe=all_series[0].timeframe,
        trades=sorted(trades, key=lambda trade: trade.exit_time),
        equity=curve,
        initial_equity=risk.initial_equity,
        signals_seen=len(trades),
    )


def _hold(
    item: _Prepared,
    direction: str,
    entry_moment: datetime,
    exit_moment: datetime,
    *,
    equity: float,
    config: CrossSectionConfig,
    costs: CostModel,
    risk: RiskModel,
) -> Trade | None:
    series = item.series
    signal_index = item.index_by_time.get(entry_moment)
    if signal_index is None or signal_index + 1 >= len(series):
        return None
    fill_index = signal_index + 1
    exit_index = item.index_by_time.get(exit_moment, len(series) - 1)
    if exit_index <= fill_index:
        return None
    current_atr = item.atr[signal_index]
    if not current_atr:
        return None

    long = direction == "LONG"
    slip = costs.slippage_pct / 100
    entry = series.open[fill_index] * (1 + slip if long else 1 - slip)
    distance = current_atr * config.stop_atr_mult
    stop = entry - distance if long else entry + distance
    if distance <= 0 or entry <= 0:
        return None
    notional = min(equity * risk.risk_pct / 100 / (distance / entry), equity * risk.max_leverage)
    units = notional / entry

    exit_price = series.close[exit_index]
    exit_time = series.time[exit_index]
    status = "REBALANCE"
    best = worst = entry
    for position in range(fill_index, exit_index + 1):
        high, low = series.high[position], series.low[position]
        best = max(best, high) if long else min(best, low)
        worst = min(worst, low) if long else max(worst, high)
        if (low <= stop) if long else (high >= stop):
            exit_price = min(series.open[position], stop) if long else max(series.open[position], stop)
            exit_time = series.time[position]
            status = "SL"
            break

    exit_fill = exit_price * (1 - slip if long else 1 + slip)
    gross = (exit_fill - entry) * units if long else (entry - exit_fill) * units
    fees = notional * costs.taker_fee_pct / 100 + units * exit_fill * costs.taker_fee_pct / 100
    pnl = gross - fees
    return Trade(
        symbol=series.symbol,
        direction=direction,
        entry_time=series.time[fill_index],
        exit_time=exit_time,
        entry_price=entry,
        exit_price=exit_fill,
        stop_price=stop,
        take_price=None,
        status=status,
        notional=notional,
        leverage=notional / equity if equity else 0.0,
        equity_before=equity,
        pnl_usd=pnl,
        pnl_pct=pnl / equity * 100 if equity else 0.0,
        r_multiple=pnl / (equity * risk.risk_pct / 100) if equity else 0.0,
        fees_usd=fees,
        bars_held=exit_index - fill_index + 1,
        mfe_r=abs(best - entry) / distance,
        mae_r=abs(worst - entry) / distance,
        reason="кросс-секция",
    )


def _ranked(
    prepared: list[_Prepared], moment: datetime, config: CrossSectionConfig
) -> list[tuple[_Prepared, float]]:
    """Symbols sorted by lookback return, strongest first."""
    rows = []
    for item in prepared:
        index = item.index_by_time.get(moment)
        if index is None:
            continue
        recent = index - config.skip
        past = recent - config.lookback
        if past < 0 or recent < 0:
            continue
        old = item.series.close[past]
        new = item.series.close[recent]
        if old <= 0:
            continue
        rows.append((item, new / old - 1))
    rows.sort(key=lambda row: row[1], reverse=not config.reverse)
    return rows


def _regime_filter(regime_series: Series | None, config: CrossSectionConfig):  # noqa: ANN202
    if regime_series is None or not config.regime_ema:
        return None
    trend = ema(regime_series.close, config.regime_ema)
    lookup = {stamp: index for index, stamp in enumerate(regime_series.time)}

    def allowed(moment: datetime) -> bool:
        index = lookup.get(moment)
        if index is None:
            return True
        value = trend[index]
        return value is None or regime_series.close[index] > value

    return allowed


def _common_timeline(all_series: list[Series]) -> list[datetime]:
    shared = set(all_series[0].time)
    for series in all_series[1:]:
        shared &= set(series.time)
    return sorted(shared)
