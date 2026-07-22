from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.backtesting.engine import (
    OI_REQUIRED_STRATEGIES,
    BacktestEngine,
    DensityEventSeries,
    load_density_events,
    load_snapshot_series,
)
from app.backtesting.metrics import objective_score
from app.config import Settings
from app.data.database import Database
from app.data.repositories import HistoricalDataRepository
from app.optimization.search_space import grid, search_space_for


class HyperOptimizer:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.engine = BacktestEngine(database, settings)

    async def run(
        self,
        *,
        strategy_key: str,
        symbol: str,
        timeframe: str = "1m",
        days: int = 30,
        limit: int = 50,
        base_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sweep the parameter grid; `timeframe` may list several ("15m,1h,4h").

        Timeframe cannot live inside the grid because it decides which candles get
        loaded, so each one is swept separately and the results are pooled.
        """
        timeframes = _timeframe_list(timeframe)
        pooled: list[dict[str, Any]] = []
        by_timeframe: dict[str, Any] = {}
        for item in timeframes:
            outcome = await self._run_timeframe(
                strategy_key=strategy_key,
                symbol=symbol,
                timeframe=item,
                days=days,
                limit=limit,
                base_params=base_params or {},
            )
            by_timeframe[item] = outcome["summary"]
            pooled.extend(outcome["results"])
        pooled.sort(key=lambda row: row["objective"], reverse=True)
        if len(timeframes) == 1:
            single = by_timeframe[timeframes[0]]
            reason = single.get("reason")
            if reason:
                return {"best": None, "results": [], "reason": reason}
        return {
            "best": pooled[0] if pooled else None,
            "results": pooled[:10],
            "by_timeframe": by_timeframe,
            "timeframes": timeframes,
            "train_candles": sum(
                int(item.get("train_candles", 0) or 0) for item in by_timeframe.values()
            ),
            "test_candles": sum(
                int(item.get("test_candles", 0) or 0) for item in by_timeframe.values()
            ),
        }

    async def _run_timeframe(
        self,
        *,
        strategy_key: str,
        symbol: str,
        timeframe: str,
        days: int,
        limit: int,
        base_params: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.database.session() as session:
            candles = await HistoricalDataRepository(session).candles(
                "bybit", symbol, timeframe, limit=None
            )
            if not candles:
                return _timeframe_skipped(timeframe, "no_historical_candles")
            # Same anchoring as BacktestEngine.run: window ends at the last candle.
            since = candles[-1].open_time - timedelta(days=days)
            candles = [candle for candle in candles if candle.open_time >= since]
            snapshots = await load_snapshot_series(session, symbol, since)
            density_events = []
            if strategy_key == "density_strategy":
                density_events = await load_density_events(session, symbol, since)
        if strategy_key in OI_REQUIRED_STRATEGIES and not len(snapshots):
            return _timeframe_skipped(timeframe, "no_oi_history")
        if strategy_key == "density_strategy" and not density_events:
            return _timeframe_skipped(timeframe, "insufficient_density_history")
        density_series = DensityEventSeries(density_events) if density_events else None
        split_at = max(1, int(len(candles) * 0.7))
        train = candles[:split_at]
        test = candles[split_at:]
        results = []
        space = search_space_for(strategy_key)
        for params in grid(space=space, limit=limit):
            params = {**base_params, **params}
            train_result = self.engine.run_on_candles(
                strategy_key=strategy_key,
                candles=train,
                timeframe=timeframe,
                params=params,
                snapshots=snapshots,
                density_events=density_series,
            )
            score = objective_score(train_result["metrics"], min_trades=self.settings.backtest.min_trades)
            test_result = self.engine.run_on_candles(
                strategy_key=strategy_key,
                candles=test,
                timeframe=timeframe,
                params=params,
                snapshots=snapshots,
                density_events=density_series,
            )
            results.append(
                {
                    "params": params,
                    "timeframe": timeframe,
                    "objective": score,
                    "train": train_result["metrics"],
                    "test": test_result["metrics"],
                }
            )
        results.sort(key=lambda row: row["objective"], reverse=True)
        return {
            "results": results,
            "summary": {
                "timeframe": timeframe,
                "combinations": len(results),
                "train_candles": len(train),
                "test_candles": len(test),
                "best": results[0] if results else None,
            },
        }


def _timeframe_list(timeframe: str | list[str]) -> list[str]:
    values = timeframe if isinstance(timeframe, list) else str(timeframe).split(",")
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    # Keep the caller's order but drop repeats, so "1h,1h,4h" costs two sweeps.
    return list(dict.fromkeys(cleaned)) or ["1m"]


def _timeframe_skipped(timeframe: str, reason: str) -> dict[str, Any]:
    """One timeframe having no data must not abort the whole sweep."""
    return {
        "results": [],
        "summary": {"timeframe": timeframe, "reason": reason, "combinations": 0},
    }
