from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from app.backtesting.simulator import SimulatedTrade


def compute_backtest_metrics(
    trades: list[SimulatedTrade],
    *,
    initial_balance: float,
    final_balance: float,
    equity_curve: list[dict[str, Any]],
) -> dict[str, Any]:
    wins = [trade for trade in trades if trade.pnl_usd > 0]
    losses = [trade for trade in trades if trade.pnl_usd < 0]
    gross_profit = sum(trade.pnl_usd for trade in wins)
    gross_loss = abs(sum(trade.pnl_usd for trade in losses))
    returns = [trade.pnl_pct for trade in trades]
    drawdowns = [float(row.get("drawdown_pct", 0.0)) for row in equity_curve]
    total = len(trades)
    return {
        "total_trades": total,
        "winrate": len(wins) / total * 100 if total else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (gross_profit or 0.0),
        "expectancy": sum(trade.pnl_usd for trade in trades) / total if total else 0.0,
        "avg_win": mean([trade.pnl_usd for trade in wins]) if wins else 0.0,
        "avg_loss": mean([trade.pnl_usd for trade in losses]) if losses else 0.0,
        "max_drawdown": max(drawdowns) if drawdowns else 0.0,
        "max_consecutive_losses": _max_consecutive(trades, winning=False),
        "max_consecutive_wins": _max_consecutive(trades, winning=True),
        "sharpe_like": mean(returns) / pstdev(returns) if len(returns) > 1 and pstdev(returns) else 0.0,
        "avg_holding_minutes": _avg_holding_minutes(trades),
        "total_fees": sum(trade.fees_usd for trade in trades),
        "net_pnl": final_balance - initial_balance,
        "return_pct": (final_balance - initial_balance) / initial_balance * 100 if initial_balance else 0.0,
        "avg_mfe": mean([trade.mfe_pct for trade in trades]) if trades else 0.0,
        "avg_mae": mean([trade.mae_pct for trade in trades]) if trades else 0.0,
        "tp_hit_rate": _status_rate(trades, "TP"),
        "sl_hit_rate": _status_rate(trades, "SL"),
        "timeout_rate": _status_rate(trades, "TIMEOUT"),
    }


def objective_score(metrics: dict[str, Any], *, min_trades: int) -> float:
    if int(metrics.get("total_trades", 0)) < min_trades:
        return -9999.0 + int(metrics.get("total_trades", 0))
    return (
        float(metrics.get("profit_factor", 0.0)) * 0.4
        + float(metrics.get("expectancy", 0.0)) * 0.3
        + float(metrics.get("return_pct", 0.0)) * 0.2
        - float(metrics.get("max_drawdown", 0.0)) * 0.3
        - _overfit_penalty(metrics)
    )


def _status_rate(trades: list[SimulatedTrade], status: str) -> float:
    return sum(1 for trade in trades if trade.status == status) / len(trades) * 100 if trades else 0.0


def _avg_holding_minutes(trades: list[SimulatedTrade]) -> float:
    if not trades:
        return 0.0
    return mean([(trade.exit_time - trade.entry_time).total_seconds() / 60 for trade in trades])


def _max_consecutive(trades: list[SimulatedTrade], *, winning: bool) -> int:
    best = 0
    current = 0
    for trade in trades:
        is_win = trade.pnl_usd > 0
        if is_win == winning:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _overfit_penalty(metrics: dict[str, Any]) -> float:
    trades = int(metrics.get("total_trades", 0))
    if trades >= 100:
        return 0.0
    return (100 - trades) / 100
