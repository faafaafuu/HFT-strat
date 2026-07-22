from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SimulatedTrade:
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    take_price: float
    status: str
    pnl_pct: float
    pnl_usd: float
    fees_usd: float
    mfe_pct: float
    mae_pct: float
    # Where the stop ended up after trailing/breakeven, and what the first target paid.
    final_stop_price: float = 0.0
    partial_pnl_usd: float = 0.0
    partial_exit_price: float | None = None
    trailing_activated: bool = False


@dataclass(frozen=True)
class ExitRules:
    """Position management during the trade, mirroring paper trading's semantics.

    Off by default: a backtest should reproduce the plain stop/take result unless the
    caller asks for management, otherwise old runs silently change meaning.
    """

    breakeven_enabled: bool = False
    breakeven_activation_rr: float = 1.0
    trailing_enabled: bool = False
    trailing_activation_rr: float = 1.0
    trailing_distance_pct: float = 0.4
    partial_tp_enabled: bool = False
    partial_tp_pct: float = 50.0
    partial_target_rr: float = 1.0

    @property
    def active(self) -> bool:
        return self.breakeven_enabled or self.trailing_enabled or self.partial_tp_enabled


def stop_take_prices(direction: str, entry: float, stop_pct: float, take_pct: float) -> tuple[float, float]:
    if direction == "LONG":
        return entry * (1 - stop_pct / 100), entry * (1 + take_pct / 100)
    return entry * (1 + stop_pct / 100), entry * (1 - take_pct / 100)


def price_at_rr(direction: str, entry: float, risk_per_unit: float, rr: float) -> float:
    """Price where the trade is `rr` times its initial risk in profit."""
    if direction == "LONG":
        return entry + risk_per_unit * rr
    return entry - risk_per_unit * rr


def simulate_exit(
    *,
    direction: str,
    entry_time: datetime,
    entry_price: float,
    future_candles,
    stop_pct: float,
    take_pct: float,
    max_holding_candles: int,
    position_size_usd: float,
    taker_fee_pct: float,
    slippage_pct: float,
    rules: ExitRules | None = None,
) -> SimulatedTrade | None:
    if not future_candles:
        return None
    rules = rules or ExitRules()
    stop_price, take_price = stop_take_prices(direction, entry_price, stop_pct, take_pct)
    initial_stop = stop_price
    risk_per_unit = abs(entry_price - initial_stop)
    partial_target = (
        price_at_rr(direction, entry_price, risk_per_unit, rules.partial_target_rr)
        if rules.partial_tp_enabled and risk_per_unit > 0
        else None
    )
    breakeven_target = (
        price_at_rr(direction, entry_price, risk_per_unit, rules.breakeven_activation_rr)
        if rules.breakeven_enabled and risk_per_unit > 0
        else None
    )
    trailing_target = (
        price_at_rr(direction, entry_price, risk_per_unit, rules.trailing_activation_rr)
        if rules.trailing_enabled and risk_per_unit > 0
        else None
    )
    best = entry_price
    worst = entry_price
    exit_price = future_candles[-1].close
    exit_time = future_candles[-1].open_time
    status = "TIMEOUT"
    remaining_size = position_size_usd
    partial_pnl = 0.0
    partial_exit_price: float | None = None
    fees = position_size_usd * (taker_fee_pct / 100)
    trailing_activated = False
    for index, candle in enumerate(future_candles[:max_holding_candles], start=1):
        if direction == "LONG":
            best = max(best, candle.high)
            worst = min(worst, candle.low)
            hit_stop = candle.low <= stop_price
            hit_take = candle.high >= take_price
        else:
            best = min(best, candle.low)
            worst = max(worst, candle.high)
            hit_stop = candle.high >= stop_price
            hit_take = candle.low <= take_price
        # Partial target pays out before the position can be stopped or completed, the
        # same order paper trading uses when both are reachable in one tick batch.
        if partial_target is not None and partial_exit_price is None and remaining_size > 0:
            reached = (
                candle.high >= partial_target if direction == "LONG" else candle.low <= partial_target
            )
            if reached:
                closed_size = remaining_size * rules.partial_tp_pct / 100
                fill = _apply_slippage(partial_target, direction, "exit", slippage_pct)
                partial_pnl = _pnl(direction, entry_price, fill, closed_size)
                fees += closed_size * (taker_fee_pct / 100)
                remaining_size -= closed_size
                partial_exit_price = fill
        if hit_stop and hit_take:
            # No lower timeframe order is available, so use conservative stop-first fill.
            exit_price = stop_price
            exit_time = candle.open_time
            status = "SL" if stop_price != entry_price else "BE"
            break
        if hit_take:
            exit_price = take_price
            exit_time = candle.open_time
            status = "TP"
            break
        if hit_stop:
            exit_price = stop_price
            exit_time = candle.open_time
            status = "SL" if stop_price != entry_price else "BE"
            break
        if index >= max_holding_candles:
            exit_price = candle.close
            exit_time = candle.open_time
            status = "TIMEOUT"
            break
        # Stops move only after the bar closes. Trailing inside the bar that made the
        # new extreme would decide the fill from the same data twice.
        if breakeven_target is not None:
            reached = (
                candle.high >= breakeven_target
                if direction == "LONG"
                else candle.low <= breakeven_target
            )
            if reached:
                stop_price = (
                    max(stop_price, entry_price)
                    if direction == "LONG"
                    else min(stop_price, entry_price)
                )
        if trailing_target is not None:
            reached = (
                candle.high >= trailing_target
                if direction == "LONG"
                else candle.low <= trailing_target
            )
            if reached:
                trailing_activated = True
                distance = rules.trailing_distance_pct / 100
                if direction == "LONG":
                    stop_price = max(stop_price, max(entry_price, best * (1 - distance)))
                else:
                    stop_price = min(stop_price, min(entry_price, best * (1 + distance)))
    entry_fill = _apply_slippage(entry_price, direction, "entry", slippage_pct)
    exit_fill = _apply_slippage(exit_price, direction, "exit", slippage_pct)
    if direction == "LONG":
        mfe_pct = (best - entry_fill) / entry_fill * 100
        mae_pct = (entry_fill - worst) / entry_fill * 100
    else:
        mfe_pct = (entry_fill - best) / entry_fill * 100
        mae_pct = (worst - entry_fill) / entry_fill * 100
    fees += remaining_size * (taker_fee_pct / 100)
    gross_pnl = partial_pnl + _pnl(direction, entry_fill, exit_fill, remaining_size)
    raw_pct = gross_pnl / position_size_usd * 100 if position_size_usd else 0.0
    return SimulatedTrade(
        direction=direction,
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry_fill,
        exit_price=exit_fill,
        stop_price=initial_stop,
        take_price=take_price,
        status=status,
        pnl_pct=raw_pct,
        pnl_usd=gross_pnl - fees,
        fees_usd=fees,
        mfe_pct=max(0.0, mfe_pct),
        mae_pct=max(0.0, mae_pct),
        final_stop_price=stop_price,
        partial_pnl_usd=partial_pnl,
        partial_exit_price=partial_exit_price,
        trailing_activated=trailing_activated,
    )


def _pnl(direction: str, entry: float, exit_price: float, size_usd: float) -> float:
    if entry <= 0 or size_usd <= 0:
        return 0.0
    move = (exit_price - entry) / entry if direction == "LONG" else (entry - exit_price) / entry
    return size_usd * move


def _apply_slippage(price: float, direction: str, fill_type: str, slippage_pct: float) -> float:
    slippage = slippage_pct / 100
    if fill_type == "entry":
        return price * (1 + slippage) if direction == "LONG" else price * (1 - slippage)
    return price * (1 - slippage) if direction == "LONG" else price * (1 + slippage)
