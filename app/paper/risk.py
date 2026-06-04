from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import PaperConfig


class PaperRiskConfig(Protocol):
    leverage: float
    risk_per_trade_pct: float
    stop_pct: float
    take_pct: float
    slippage_pct: float


@dataclass(frozen=True)
class PaperPlan:
    entry_price: float
    stop_price: float
    take_price: float
    position_size_usd: float
    risk_usd: float
    leverage: float


def apply_entry_slippage(price: float, direction: str, slippage_pct: float) -> float:
    slip = slippage_pct / 100
    if direction.upper() == "LONG":
        return price * (1 + slip)
    return price * (1 - slip)


def apply_exit_slippage(price: float, direction: str, slippage_pct: float) -> float:
    slip = slippage_pct / 100
    if direction.upper() == "LONG":
        return price * (1 - slip)
    return price * (1 + slip)


def calculate_paper_plan(
    balance: float,
    signal_price: float,
    direction: str,
    config: PaperConfig | PaperRiskConfig,
) -> PaperPlan:
    entry = apply_entry_slippage(signal_price, direction, config.slippage_pct)
    stop_distance = config.stop_pct / 100
    take_distance = config.take_pct / 100
    if direction.upper() == "LONG":
        stop = entry * (1 - stop_distance)
        take = entry * (1 + take_distance)
    else:
        stop = entry * (1 + stop_distance)
        take = entry * (1 - take_distance)

    risk_usd = balance * config.risk_per_trade_pct / 100
    risk_position_cap = risk_usd / stop_distance if stop_distance > 0 else 0
    leverage_cap = balance * config.leverage
    position_size = max(0.0, min(risk_position_cap, leverage_cap))
    actual_risk = position_size * stop_distance
    return PaperPlan(
        entry_price=entry,
        stop_price=stop,
        take_price=take,
        position_size_usd=position_size,
        risk_usd=actual_risk,
        leverage=config.leverage,
    )


def pnl_for_exit(
    direction: str,
    entry_price: float,
    exit_price: float,
    position_size_usd: float,
) -> float:
    if position_size_usd <= 0 or entry_price <= 0:
        return 0.0
    qty = position_size_usd / entry_price
    if direction.upper() == "LONG":
        return (exit_price - entry_price) * qty
    return (entry_price - exit_price) * qty


def fee_for_notional(position_size_usd: float, fee_pct: float) -> float:
    return position_size_usd * fee_pct / 100


def rr_for_price(direction: str, entry_price: float, stop_price: float, price: float) -> float:
    risk_distance = abs(entry_price - stop_price)
    if risk_distance <= 0:
        return 0.0
    if direction.upper() == "LONG":
        return (price - entry_price) / risk_distance
    return (entry_price - price) / risk_distance
