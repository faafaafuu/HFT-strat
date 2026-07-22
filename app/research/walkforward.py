"""Walk-forward validation.

The point is to never report a number that was chosen with knowledge of the period it is
reported on. Each window picks parameters on its training slice and then trades the next
slice untouched; only those forward slices are concatenated into the reported curve.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.research.data import Series
from app.research.harness import CostModel, RiskModel, RunResult, Trade
from app.research.portfolio import candidates, combine
from app.research.stats import summarise


@dataclass(frozen=True)
class Window:
    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    params: dict[str, Any]
    train_summary: dict[str, Any]
    test_summary: dict[str, Any]


@dataclass(frozen=True)
class WalkForwardResult:
    windows: list[Window]
    forward: RunResult

    @property
    def summary(self) -> dict[str, Any]:
        return summarise(self.forward)


def walk_forward(
    all_series: Sequence[Series],
    build_strategy: Callable[[dict[str, Any]], Any],
    grid: list[dict[str, Any]],
    *,
    costs: CostModel,
    risk: RiskModel,
    windows: int = 5,
    train_fraction: float = 0.6,
    max_concurrent: int = 5,
    score: Callable[[dict[str, Any]], float] | None = None,
) -> WalkForwardResult:
    """Roll `windows` train/test pairs across the data and chain the test segments."""
    length = min(len(series) for series in all_series)
    if length < windows * 20:
        raise ValueError("Слишком мало свечей для walk-forward")
    score = score or _default_score
    # Anchored-length rolling windows: every step trades the same amount of unseen data.
    test_span = int(length * (1 - train_fraction) / windows)
    train_span = int(length * train_fraction)
    reported: list[Window] = []
    forward_trades: list[Trade] = []
    equity = risk.initial_equity
    curve: list[tuple[datetime, float]] = []

    for step in range(windows):
        test_end = length - (windows - step - 1) * test_span
        test_start = test_end - test_span
        train_start = max(0, test_start - train_span)
        if test_start <= train_start:
            continue

        best_params: dict[str, Any] | None = None
        best_score = float("-inf")
        best_train: dict[str, Any] = {}
        for params in grid:
            train_result = _portfolio_run(
                all_series, build_strategy, params, costs, risk, train_start, test_start, max_concurrent
            )
            summary = summarise(train_result)
            if not summary.get("trades"):
                continue
            value = score(summary)
            if value > best_score:
                best_score, best_params, best_train = value, params, summary
        if best_params is None:
            continue

        # The forward slice is traded with the equity the previous windows produced, so the
        # compounding is the one an account would actually have experienced.
        step_risk = RiskModel(**{**risk.__dict__, "initial_equity": equity})
        test_result = _portfolio_run(
            all_series, build_strategy, best_params, costs, step_risk, test_start, test_end, max_concurrent
        )
        forward_trades.extend(test_result.trades)
        for point in test_result.equity:
            curve.append(point)
        equity = test_result.final_equity
        reported.append(
            Window(
                index=step,
                train_start=all_series[0].time[train_start],
                train_end=all_series[0].time[test_start],
                test_start=all_series[0].time[test_start],
                test_end=all_series[0].time[min(test_end, len(all_series[0]) - 1)],
                params=best_params,
                train_summary=best_train,
                test_summary=summarise(test_result),
            )
        )

    forward = RunResult(
        symbol="ПОРТФЕЛЬ-WF",
        timeframe=all_series[0].timeframe,
        trades=sorted(forward_trades, key=lambda trade: trade.exit_time),
        equity=sorted(curve, key=lambda point: point[0]),
        initial_equity=risk.initial_equity,
        signals_seen=sum(int(window.test_summary.get("trades", 0) or 0) for window in reported),
    )
    return WalkForwardResult(windows=reported, forward=forward)


def _portfolio_run(
    all_series: Sequence[Series],
    build_strategy: Callable[[dict[str, Any]], Any],
    params: dict[str, Any],
    costs: CostModel,
    risk: RiskModel,
    start: int,
    end: int,
    max_concurrent: int,
) -> RunResult:
    per_symbol = [
        candidates(series, build_strategy(params), costs=costs, risk=risk, start=start, end=end)
        for series in all_series
    ]
    if not any(per_symbol):
        return RunResult(
            symbol="ПОРТФЕЛЬ",
            timeframe=all_series[0].timeframe,
            trades=[],
            equity=[],
            initial_equity=risk.initial_equity,
            signals_seen=0,
        )
    return combine(
        per_symbol,
        risk=risk,
        max_concurrent=max_concurrent,
        timeframe=all_series[0].timeframe,
    )


def _default_score(summary: dict[str, Any]) -> float:
    """Prefer a real edge over a lucky one: profit factor, damped by too few trades."""
    trades = int(summary.get("trades", 0))
    if trades < 30:
        return float("-inf")
    return float(summary.get("expectancy_r", 0.0)) * min(1.0, trades / 100)
