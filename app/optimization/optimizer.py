from __future__ import annotations

from typing import Any

from app.backtesting.engine import BacktestEngine
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
    ) -> dict[str, Any]:
        async with self.database.session() as session:
            candles = await HistoricalDataRepository(session).candles(
                "bybit", symbol, timeframe, limit=None
            )
        if not candles:
            return {"best": None, "results": [], "reason": "no_historical_candles"}
        split_at = max(1, int(len(candles) * 0.7))
        train = candles[:split_at]
        test = candles[split_at:]
        results = []
        space = DENSITY_SEARCH_SPACE if strategy_key == "density_strategy" else None
        for params in grid(space=space, limit=limit):
            train_result = self.engine.run_on_candles(
                strategy_key=strategy_key,
                candles=train,
                timeframe=timeframe,
                params=params,
            )
            score = objective_score(train_result["metrics"], min_trades=self.settings.backtest.min_trades)
            test_result = self.engine.run_on_candles(
                strategy_key=strategy_key,
                candles=test,
                timeframe=timeframe,
                params=params,
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
