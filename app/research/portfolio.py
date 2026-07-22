"""Multi-symbol runs on one shared account.

Running each symbol on its own equity and adding the returns up would be a different
system from the one that can actually be traded: real capital is shared, positions
compete for it, and the drawdowns overlap. So candidates are generated per symbol and
then filled chronologically against a single equity curve.

A trade's return per unit of notional does not depend on how large the position was —
fees are proportional too — so outcomes can be precomputed once and sized later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.research.data import Series
from app.research.harness import CostModel, ResearchStrategy, RiskModel, RunResult, Trade, run


@dataclass(frozen=True)
class Candidate:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    direction: str
    stop_distance: float
    pnl_fraction: float
    status: str
    bars_held: int
    mfe_r: float
    mae_r: float


def candidates(
    series: Series,
    strategy: ResearchStrategy,
    *,
    costs: CostModel,
    risk: RiskModel,
    start: int = 0,
    end: int | None = None,
) -> list[Candidate]:
    """Per-symbol trades, expressed so they can be re-sized against any equity."""
    result = run(series, strategy, costs=costs, risk=risk, start=start, end=end)
    rows = []
    for trade in result.trades:
        if trade.notional <= 0:
            continue
        rows.append(
            Candidate(
                symbol=trade.symbol,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                direction=trade.direction,
                stop_distance=abs(trade.entry_price - trade.stop_price) / trade.entry_price,
                pnl_fraction=trade.pnl_usd / trade.notional,
                status=trade.status,
                bars_held=trade.bars_held,
                mfe_r=trade.mfe_r,
                mae_r=trade.mae_r,
            )
        )
    return rows


def combine(
    per_symbol: list[list[Candidate]],
    *,
    risk: RiskModel,
    max_concurrent: int = 5,
    max_gross_leverage: float = 10.0,
    timeframe: str = "1h",
) -> RunResult:
    """Fill candidates in time order against one equity, capping concurrent exposure."""
    pool = sorted(
        (item for rows in per_symbol for item in rows), key=lambda row: row.entry_time
    )
    equity = risk.initial_equity
    open_positions: list[tuple[datetime, float, float, Candidate]] = []
    trades: list[Trade] = []
    curve: list[tuple[datetime, float]] = []

    def settle(until: datetime) -> None:
        nonlocal equity
        while open_positions:
            open_positions.sort(key=lambda item: item[0])
            exit_time, notional, entry_equity, candidate = open_positions[0]
            if exit_time > until:
                return
            open_positions.pop(0)
            pnl = notional * candidate.pnl_fraction
            equity += pnl
            trades.append(_as_trade(candidate, notional, entry_equity, pnl, risk, timeframe))
            curve.append((exit_time, equity))

    for candidate in pool:
        settle(candidate.entry_time)
        if len(open_positions) >= max_concurrent or candidate.stop_distance <= 0:
            continue
        if equity <= 0:
            break
        committed = sum(item[1] for item in open_positions)
        headroom = max(0.0, equity * max_gross_leverage - committed)
        notional = min(
            equity * risk.risk_pct / 100 / candidate.stop_distance,
            equity * risk.max_leverage,
            headroom,
        )
        if notional <= 0:
            continue
        open_positions.append((candidate.exit_time, notional, equity, candidate))
    settle(datetime.max.replace(tzinfo=pool[0].entry_time.tzinfo) if pool else datetime.max)

    trades.sort(key=lambda trade: trade.exit_time)
    curve.sort(key=lambda point: point[0])
    return RunResult(
        symbol="ПОРТФЕЛЬ",
        timeframe=timeframe,
        trades=trades,
        equity=curve,
        initial_equity=risk.initial_equity,
        signals_seen=len(pool),
    )


def _as_trade(
    candidate: Candidate,
    notional: float,
    equity_before: float,
    pnl: float,
    risk: RiskModel,
    timeframe: str,
) -> Trade:
    return Trade(
        symbol=candidate.symbol,
        direction=candidate.direction,
        entry_time=candidate.entry_time,
        exit_time=candidate.exit_time,
        entry_price=0.0,
        exit_price=0.0,
        stop_price=0.0,
        take_price=None,
        status=candidate.status,
        notional=notional,
        leverage=notional / equity_before if equity_before else 0.0,
        equity_before=equity_before,
        pnl_usd=pnl,
        pnl_pct=pnl / equity_before * 100 if equity_before else 0.0,
        r_multiple=pnl / (equity_before * risk.risk_pct / 100) if equity_before else 0.0,
        fees_usd=0.0,
        bars_held=candidate.bars_held,
        mfe_r=candidate.mfe_r,
        mae_r=candidate.mae_r,
        reason=timeframe,
    )
