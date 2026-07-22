"""Metrics that answer the acceptance criteria directly.

Monthly returns are compounded from the equity curve, not averaged from trade PnL: at 2%
risk with compounding a mean-of-trades number would overstate what the account actually did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median, pstdev
from typing import Any

from app.research.harness import RunResult, Trade

# The acceptance table from the brief, kept in one place so no run can quietly use its own.
STAGE_1 = {
    "median_month_pct": 20.0,
    "profitable_months_pct": 70.0,
    "worst_month_pct": -15.0,
    "profit_factor": 1.6,
    "winrate_pct": 45.0,
    "winrate_pct_high_pf": 35.0,
    "profit_factor_high": 2.2,
    "max_drawdown_pct": 25.0,
    "trades": 200,
    "trades_per_month": 15.0,
    "sharpe_like": 0.15,
}

STAGE_2 = {
    **STAGE_1,
    "median_month_pct": 50.0,
    "profitable_months_pct": 65.0,
    "profit_factor": 2.0,
    "max_drawdown_pct": 35.0,
}


@dataclass(frozen=True)
class MonthRow:
    month: str
    return_pct: float
    trades: int
    equity_end: float
    drawdown_pct: float


def monthly_table(result: RunResult) -> list[MonthRow]:
    if not result.trades:
        return []
    rows: list[MonthRow] = []
    equity = result.initial_equity
    peak = equity
    current = _month(result.trades[0].exit_time)
    start_equity = equity
    count = 0
    worst_dd = 0.0
    for trade in result.trades:
        month = _month(trade.exit_time)
        if month != current:
            rows.append(
                MonthRow(current, (equity / start_equity - 1) * 100, count, equity, worst_dd)
            )
            current, start_equity, count, worst_dd = month, equity, 0, 0.0
        equity += trade.pnl_usd
        count += 1
        peak = max(peak, equity)
        worst_dd = max(worst_dd, (peak - equity) / peak * 100 if peak > 0 else 0.0)
    rows.append(MonthRow(current, (equity / start_equity - 1) * 100, count, equity, worst_dd))
    return rows


def summarise(result: RunResult) -> dict[str, Any]:
    trades = result.trades
    if not trades:
        return {"trades": 0, "reason": "нет сделок"}
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    months = monthly_table(result)
    month_returns = [row.return_pct for row in months]
    equity_points = [result.initial_equity] + _equity_points(trades, result.initial_equity)
    returns = [t.pnl_pct for t in trades]
    final = equity_points[-1]
    return {
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "trades": len(trades),
        "winrate_pct": len(wins) / len(trades) * 100,
        "profit_factor": gross_profit / gross_loss if gross_loss else float(bool(gross_profit)) * 99,
        "expectancy_r": sum(t.r_multiple for t in trades) / len(trades),
        "net_pnl": final - result.initial_equity,
        "total_return_pct": (final / result.initial_equity - 1) * 100,
        "median_month_pct": median(month_returns) if month_returns else 0.0,
        "mean_month_pct": sum(month_returns) / len(month_returns) if month_returns else 0.0,
        "worst_month_pct": min(month_returns) if month_returns else 0.0,
        "best_month_pct": max(month_returns) if month_returns else 0.0,
        "profitable_months_pct": (
            sum(1 for value in month_returns if value > 0) / len(month_returns) * 100
            if month_returns
            else 0.0
        ),
        "months": len(months),
        "trades_per_month": len(trades) / len(months) if months else 0.0,
        "max_drawdown_pct": _max_drawdown(equity_points),
        "sharpe_like": (
            sum(returns) / len(returns) / pstdev(returns)
            if len(returns) > 1 and pstdev(returns)
            else 0.0
        ),
        "avg_leverage": sum(t.leverage for t in trades) / len(trades),
        "max_leverage": max(t.leverage for t in trades),
        "avg_bars_held": sum(t.bars_held for t in trades) / len(trades),
        "fees_usd": sum(t.fees_usd for t in trades),
        "fees_share_of_gross": (
            sum(t.fees_usd for t in trades) / gross_profit * 100 if gross_profit else 0.0
        ),
        "signals_seen": result.signals_seen,
        "period_start": trades[0].entry_time.isoformat(),
        "period_end": trades[-1].exit_time.isoformat(),
    }


def verdict(summary: dict[str, Any], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    """Line-by-line pass/fail against the acceptance table."""
    limits = thresholds or STAGE_1
    if not summary.get("trades"):
        return {"passed": False, "checks": [], "reason": "нет сделок"}
    pf = float(summary["profit_factor"])
    winrate_limit = (
        limits["winrate_pct_high_pf"] if pf >= limits["profit_factor_high"] else limits["winrate_pct"]
    )
    checks = [
        _check("Медианная месячная доходность", summary["median_month_pct"], limits["median_month_pct"], "≥", "%"),
        _check("Доля прибыльных месяцев", summary["profitable_months_pct"], limits["profitable_months_pct"], "≥", "%"),
        _check("Худший месяц", summary["worst_month_pct"], limits["worst_month_pct"], "≥", "%"),
        _check("Profit factor", pf, limits["profit_factor"], "≥", ""),
        _check("Winrate", summary["winrate_pct"], winrate_limit, "≥", "%"),
        _check("Max drawdown", summary["max_drawdown_pct"], limits["max_drawdown_pct"], "≤", "%"),
        _check("Сделок всего", summary["trades"], limits["trades"], "≥", ""),
        _check("Сделок в месяц", summary["trades_per_month"], limits["trades_per_month"], "≥", ""),
        _check("Sharpe-like", summary["sharpe_like"], limits["sharpe_like"], "≥", ""),
    ]
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def _check(name: str, value: float, limit: float, direction: str, unit: str) -> dict[str, Any]:
    passed = value >= limit if direction == "≥" else value <= limit
    return {
        "name": name,
        "value": round(float(value), 3),
        "limit": limit,
        "direction": direction,
        "unit": unit,
        "passed": bool(passed),
    }


def _equity_points(trades: list[Trade], initial: float) -> list[float]:
    equity = initial
    points = []
    for trade in trades:
        equity += trade.pnl_usd
        points.append(equity)
    return points


def _max_drawdown(points: list[float]) -> float:
    peak = points[0]
    worst = 0.0
    for value in points:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100)
    return worst


def _month(moment: datetime) -> str:
    return f"{moment.year}-{moment.month:02d}"
