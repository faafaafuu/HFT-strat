"""Execution model for research runs.

Deliberately separate from `app.backtesting.engine`: that one sizes every position at the
full balance and reports dollar PnL, which cannot answer "what is the monthly return on
capital at 2% risk". Here sizing is risk-based and compounding, and the entry is taken on
the bar *after* the signal so no decision can read a price it could not have seen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.research.data import Series


@dataclass(frozen=True)
class Entry:
    """What a strategy asks for, in absolute prices — no percentage round-trips."""

    direction: str
    stop: float
    take: float | None = None
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class ResearchStrategy(Protocol):
    key: str
    warmup: int

    def signal(self, series: Series, index: int) -> Entry | None:
        """Decide from bars 0..index inclusive; the trade will open at bar index+1."""


@dataclass(frozen=True)
class CostModel:
    """Bybit perpetual taker costs. Slippage is per side, on top of the fee."""

    taker_fee_pct: float = 0.055
    slippage_pct: float = 0.02

    def doubled_slippage(self) -> CostModel:
        return CostModel(taker_fee_pct=self.taker_fee_pct, slippage_pct=self.slippage_pct * 2)


@dataclass(frozen=True)
class RiskModel:
    risk_pct: float = 2.0
    max_leverage: float = 10.0
    initial_equity: float = 10_000.0
    # Position management, all expressed in R (multiples of the initial stop distance).
    breakeven_at_r: float | None = None
    trail_from_r: float | None = None
    trail_distance_r: float = 1.0
    partial_at_r: float | None = None
    partial_fraction: float = 0.5
    max_bars: int = 120


@dataclass(frozen=True)
class Trade:
    symbol: str
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    take_price: float | None
    status: str
    notional: float
    leverage: float
    equity_before: float
    pnl_usd: float
    pnl_pct: float
    r_multiple: float
    fees_usd: float
    bars_held: int
    mfe_r: float
    mae_r: float
    reason: str


@dataclass(frozen=True)
class RunResult:
    symbol: str
    timeframe: str
    trades: list[Trade]
    equity: list[tuple[datetime, float]]
    initial_equity: float
    signals_seen: int

    @property
    def final_equity(self) -> float:
        return self.equity[-1][1] if self.equity else self.initial_equity


def run(
    series: Series,
    strategy: ResearchStrategy,
    *,
    costs: CostModel | None = None,
    risk: RiskModel | None = None,
    start: int = 0,
    end: int | None = None,
    initial_equity: float | None = None,
) -> RunResult:
    costs = costs or CostModel()
    risk = risk or RiskModel()
    # Indicators are computed once per series rather than per bar; a sweep re-runs the
    # same series hundreds of times and recomputing inside signal() dominates the cost.
    prepare = getattr(strategy, "prepare", None)
    if callable(prepare):
        prepare(series)
    equity = initial_equity if initial_equity is not None else risk.initial_equity
    first = max(start, strategy.warmup)
    last = (len(series) if end is None else min(end, len(series))) - 1

    trades: list[Trade] = []
    curve: list[tuple[datetime, float]] = [(series.time[first], equity)] if first < len(series) else []
    signals_seen = 0
    index = first
    while index < last:
        entry = strategy.signal(series, index)
        if entry is None:
            index += 1
            continue
        signals_seen += 1
        trade = _open_and_close(
            series, index, entry, equity=equity, costs=costs, risk=risk, limit=last
        )
        if trade is None:
            index += 1
            continue
        trades.append(trade)
        equity += trade.pnl_usd
        curve.append((trade.exit_time, equity))
        if equity <= 0:
            break
        # One position at a time: resume scanning after the exit bar.
        index = max(index + 1, _bar_of(series, trade.exit_time, index))
    return RunResult(
        symbol=series.symbol,
        timeframe=series.timeframe,
        trades=trades,
        equity=curve,
        initial_equity=initial_equity if initial_equity is not None else risk.initial_equity,
        signals_seen=signals_seen,
    )


def _open_and_close(
    series: Series,
    index: int,
    entry: Entry,
    *,
    equity: float,
    costs: CostModel,
    risk: RiskModel,
    limit: int,
) -> Trade | None:
    """Fill at the next bar's open, then walk forward bar by bar until an exit triggers."""
    fill_index = index + 1
    if fill_index > limit:
        return None
    long = entry.direction == "LONG"
    raw_entry = series.open[fill_index]
    slip = costs.slippage_pct / 100
    entry_price = raw_entry * (1 + slip) if long else raw_entry * (1 - slip)
    stop = entry.stop
    risk_per_unit = abs(entry_price - stop)
    if risk_per_unit <= 0 or entry_price <= 0:
        return None
    # A stop further away buys a smaller position: the dollar risk is what is held fixed.
    stop_distance = risk_per_unit / entry_price
    notional = min(equity * risk.risk_pct / 100 / stop_distance, equity * risk.max_leverage)
    if notional <= 0:
        return None
    units = notional / entry_price

    take = entry.take
    breakeven_price = _price_at_r(long, entry_price, risk_per_unit, risk.breakeven_at_r)
    trail_arm = _price_at_r(long, entry_price, risk_per_unit, risk.trail_from_r)
    partial_price = _price_at_r(long, entry_price, risk_per_unit, risk.partial_at_r)

    best = entry_price
    worst = entry_price
    remaining = 1.0
    realised = 0.0
    partial_fees = 0.0
    status = "TIMEOUT"
    exit_price = series.close[min(limit, fill_index)]
    exit_time = series.time[min(limit, fill_index)]
    bars = 0
    last_bar = min(limit, fill_index + risk.max_bars - 1)

    for position in range(fill_index, last_bar + 1):
        bars = position - fill_index + 1
        high, low = series.high[position], series.low[position]
        best = max(best, high) if long else min(best, low)
        worst = min(worst, low) if long else max(worst, high)

        # Partial first: it pays out on the way to the target, before any stop can move.
        if partial_price is not None and remaining > 0 and _favourable(long, high, low, partial_price):
            filled = risk.partial_fraction * remaining
            realised += _pnl(long, entry_price, partial_price, units * filled)
            partial_fees += units * filled * partial_price * costs.taker_fee_pct / 100
            remaining -= filled
            partial_price = None
        if breakeven_price is not None and _favourable(long, high, low, breakeven_price):
            stop = max(stop, entry_price) if long else min(stop, entry_price)
            breakeven_price = None
        if trail_arm is not None and _favourable(long, high, low, trail_arm):
            trailed = (
                best - risk.trail_distance_r * risk_per_unit
                if long
                else best + risk.trail_distance_r * risk_per_unit
            )
            stop = max(stop, trailed) if long else min(stop, trailed)

        # Stop before take inside the same bar: the pessimistic order, since the bar does
        # not say which came first and assuming the good one inflates every result.
        if _adverse(long, high, low, stop):
            exit_price = _gapped(long, series.open[position], stop, stopping=True)
            exit_time = series.time[position]
            status = "SL" if remaining == 1.0 else "SL_AFTER_PARTIAL"
            break
        if take is not None and _favourable(long, high, low, take):
            exit_price = _gapped(long, series.open[position], take, stopping=False)
            exit_time = series.time[position]
            status = "TP"
            break
        exit_price = series.close[position]
        exit_time = series.time[position]

    exit_fill = exit_price * (1 - slip) if long else exit_price * (1 + slip)
    realised += _pnl(long, entry_price, exit_fill, units * remaining)
    fees = notional * costs.taker_fee_pct / 100 + units * remaining * exit_fill * costs.taker_fee_pct / 100
    fees += partial_fees
    pnl = realised - fees
    return Trade(
        symbol=series.symbol,
        direction=entry.direction,
        entry_time=series.time[fill_index],
        exit_time=exit_time,
        entry_price=entry_price,
        exit_price=exit_fill,
        stop_price=entry.stop,
        take_price=entry.take,
        status=status,
        notional=notional,
        leverage=notional / equity if equity else 0.0,
        equity_before=equity,
        pnl_usd=pnl,
        pnl_pct=pnl / equity * 100 if equity else 0.0,
        r_multiple=pnl / (equity * risk.risk_pct / 100) if equity else 0.0,
        fees_usd=fees,
        bars_held=bars,
        mfe_r=abs(best - entry_price) / risk_per_unit,
        mae_r=abs(worst - entry_price) / risk_per_unit,
        reason=entry.reason,
    )


def _pnl(long: bool, entry: float, exit_price: float, units: float) -> float:
    return (exit_price - entry) * units if long else (entry - exit_price) * units


def _price_at_r(long: bool, entry: float, risk_per_unit: float, r: float | None) -> float | None:
    if r is None:
        return None
    return entry + risk_per_unit * r if long else entry - risk_per_unit * r


def _favourable(long: bool, high: float, low: float, level: float) -> bool:
    """Did the bar reach a level that is in the trade's favour (take, partial, trail arm)?"""
    return high >= level if long else low <= level


def _adverse(long: bool, high: float, low: float, level: float) -> bool:
    """Did the bar reach a level that is against the trade (the stop)?"""
    return low <= level if long else high >= level


def _gapped(long: bool, bar_open: float, level: float, *, stopping: bool) -> float:
    """A bar that opens past the level fills at the open, not at the level."""
    if stopping:
        return min(bar_open, level) if long else max(bar_open, level)
    return max(bar_open, level) if long else min(bar_open, level)


def _bar_of(series: Series, moment: datetime, hint: int) -> int:
    for position in range(hint, len(series)):
        if series.time[position] >= moment:
            return position
    return len(series)
