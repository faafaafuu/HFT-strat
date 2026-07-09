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
from app.optimization.search_space import DENSITY_SEARCH_SPACE, grid


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
        base_params = base_params or {}
        async with self.database.session() as session:
            candles = await HistoricalDataRepository(session).candles(
                "bybit", symbol, timeframe, limit=None
            )
            if not candles:
                return {"best": None, "results": [], "reason": "no_historical_candles"}
            # Same anchoring as BacktestEngine.run: window ends at the last candle.
            since = candles[-1].open_time - timedelta(days=days)
            candles = [candle for candle in candles if candle.open_time >= since]
            snapshots = await load_snapshot_series(session, symbol, since)
            density_events = []
            if strategy_key == "density_strategy":
                density_events = await load_density_events(session, symbol, since)
        if strategy_key in OI_REQUIRED_STRATEGIES and not len(snapshots):
            return {"best": None, "results": [], "reason": "no_oi_history"}
        if strategy_key == "density_strategy" and not density_events:
            return {"best": None, "results": [], "reason": "insufficient_density_history"}
        density_series = DensityEventSeries(density_events) if density_events else None
        split_at = max(1, int(len(candles) * 0.7))
        train = candles[:split_at]
        test = candles[split_at:]
        results = []
        space = DENSITY_SEARCH_SPACE if strategy_key == "density_strategy" else None
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
                    "objective": score,
                    "train": train_result["metrics"],
                    "test": test_result["metrics"],
                }
            )
        results.sort(key=lambda row: row["objective"], reverse=True)
        return {
            "best": results[0] if results else None,
            "results": results[:10],
            "train_candles": len(train),
            "test_candles": len(test),
        }
