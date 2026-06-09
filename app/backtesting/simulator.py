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


def stop_take_prices(direction: str, entry: float, stop_pct: float, take_pct: float) -> tuple[float, float]:
    if direction == "LONG":
        return entry * (1 - stop_pct / 100), entry * (1 + take_pct / 100)
    return entry * (1 + stop_pct / 100), entry * (1 - take_pct / 100)


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
) -> SimulatedTrade | None:
    if not future_candles:
        return None
    stop_price, take_price = stop_take_prices(direction, entry_price, stop_pct, take_pct)
    best = entry_price
    worst = entry_price
    exit_price = future_candles[-1].close
    exit_time = future_candles[-1].open_time
    status = "TIMEOUT"
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
        if hit_stop and hit_take:
            # No lower timeframe order is available, so use conservative stop-first fill.
            exit_price = stop_price
            exit_time = candle.open_time
            status = "SL"
            break
        if hit_take:
            exit_price = take_price
            exit_time = candle.open_time
            status = "TP"
            break
        if hit_stop:
            exit_price = stop_price
            exit_time = candle.open_time
            status = "SL"
            break
        if index >= max_holding_candles:
            exit_price = candle.close
            exit_time = candle.open_time
            status = "TIMEOUT"
            break
    entry_fill = _apply_slippage(entry_price, direction, "entry", slippage_pct)
    exit_fill = _apply_slippage(exit_price, direction, "exit", slippage_pct)
    if direction == "LONG":
        raw_pct = (exit_fill - entry_fill) / entry_fill * 100
        mfe_pct = (best - entry_fill) / entry_fill * 100
        mae_pct = (entry_fill - worst) / entry_fill * 100
    else:
        raw_pct = (entry_fill - exit_fill) / entry_fill * 100
        mfe_pct = (entry_fill - best) / entry_fill * 100
        mae_pct = (worst - entry_fill) / entry_fill * 100
    gross_pnl = position_size_usd * raw_pct / 100
    fees = position_size_usd * (taker_fee_pct / 100) * 2
    return SimulatedTrade(
        direction=direction,
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry_fill,
        exit_price=exit_fill,
        stop_price=stop_price,
        take_price=take_price,
        status=status,
        pnl_pct=raw_pct,
        pnl_usd=gross_pnl - fees,
        fees_usd=fees,
        mfe_pct=max(0.0, mfe_pct),
        mae_pct=max(0.0, mae_pct),
    )


def _apply_slippage(price: float, direction: str, fill_type: str, slippage_pct: float) -> float:
    slippage = slippage_pct / 100
    if fill_type == "entry":
        return price * (1 + slippage) if direction == "LONG" else price * (1 - slippage)
    return price * (1 - slippage) if direction == "LONG" else price * (1 + slippage)
