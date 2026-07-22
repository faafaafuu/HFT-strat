from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
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
from app.data.repositories import HistoricalDataRepository, HyperoptCacheRepository
from app.jobs.cancellation import CancellationToken
from app.optimization.search_space import grid, search_space_for

# Bump when a change to the engine, the simulator or the strategies would make a stored
# evaluation wrong. Cached rows from older versions are then ignored.
EVALUATION_VERSION = 1


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
        cancellation: CancellationToken | None = None,
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
                cancellation=cancellation,
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
            "from_cache": sum(int(item.get("from_cache", 0) or 0) for item in by_timeframe.values()),
            "computed": sum(int(item.get("computed", 0) or 0) for item in by_timeframe.values()),
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
        cancellation: CancellationToken | None = None,
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
        period_start = candles[0].open_time
        period_end = candles[-1].open_time

        combinations = [{**base_params, **params} for params in grid(space=space_for(strategy_key), limit=limit)]
        keys = [
            _cache_key(
                strategy_key=strategy_key,
                symbol=symbol,
                timeframe=timeframe,
                params=params,
                period_start=period_start,
                period_end=period_end,
                split_at=split_at,
            )
            for params in combinations
        ]
        async with self.database.session() as session:
            cached = await HyperoptCacheRepository(session).get_many(keys)

        results = []
        fresh: list[tuple[str, dict[str, Any], float, dict[str, Any], dict[str, Any]]] = []
        try:
            await self._evaluate(
                combinations=combinations,
                keys=keys,
                cached=cached,
                results=results,
                fresh=fresh,
                strategy_key=strategy_key,
                timeframe=timeframe,
                train=train,
                test=test,
                snapshots=snapshots,
                density_series=density_series,
                cancellation=cancellation,
            )
        finally:
            # Whatever was computed before the stop stays in the cache: a cancelled sweep
            # is not wasted, the next one resumes from here.
            await self._store(
                fresh,
                strategy_key=strategy_key,
                symbol=symbol,
                timeframe=timeframe,
                period_start=period_start,
                period_end=period_end,
            )

        results.sort(key=lambda row: row["objective"], reverse=True)
        return {
            "results": results,
            "summary": {
                "timeframe": timeframe,
                "combinations": len(results),
                "from_cache": len(results) - len(fresh),
                "computed": len(fresh),
                "train_candles": len(train),
                "test_candles": len(test),
                "best": results[0] if results else None,
            },
        }

    async def _evaluate(
        self,
        *,
        combinations: list[dict[str, Any]],
        keys: list[str],
        cached: dict[str, dict[str, Any]],
        results: list[dict[str, Any]],
        fresh: list[tuple[str, dict[str, Any], float, dict[str, Any], dict[str, Any]]],
        strategy_key: str,
        timeframe: str,
        train: list[Any],
        test: list[Any],
        snapshots: Any,
        density_series: DensityEventSeries | None,
        cancellation: CancellationToken | None,
    ) -> None:
        for params, key in zip(combinations, keys, strict=True):
            hit = cached.get(key)
            if hit is not None:
                results.append({**hit, "params": params, "timeframe": timeframe})
                continue
            # Checked before the work, not after: one combination is the smallest unit
            # that can be abandoned without leaving a half-evaluated row behind.
            if cancellation is not None:
                await cancellation.raise_if_cancelled()
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
                    "cached": False,
                }
            )
            fresh.append((key, params, score, train_result["metrics"], test_result["metrics"]))

    async def _store(
        self,
        fresh: list[tuple[str, dict[str, Any], float, dict[str, Any], dict[str, Any]]],
        *,
        strategy_key: str,
        symbol: str,
        timeframe: str,
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        if not fresh:
            return
        async with self.database.session() as session:
            repo = HyperoptCacheRepository(session)
            for key, params, score, train_metrics, test_metrics in fresh:
                await repo.store(
                    cache_key=key,
                    strategy_key=strategy_key,
                    symbol=symbol,
                    timeframe=timeframe,
                    params=params,
                    period_start=period_start,
                    period_end=period_end,
                    objective=score,
                    train=train_metrics,
                    test=test_metrics,
                )


def space_for(strategy_key: str) -> dict[str, list[Any]]:
    return search_space_for(strategy_key)


def _cache_key(
    *,
    strategy_key: str,
    symbol: str,
    timeframe: str,
    params: dict[str, Any],
    period_start: datetime,
    period_end: datetime,
    split_at: int,
) -> str:
    """Everything that changes the outcome, and nothing that doesn't.

    The candle window is identified by its own first and last timestamps rather than by
    the requested `days`, so asking for 90 days today and tomorrow reuses the same rows
    as long as no new candles arrived.
    """
    payload = json.dumps(
        {
            "version": EVALUATION_VERSION,
            "strategy": strategy_key,
            "symbol": symbol,
            "timeframe": timeframe,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "split_at": split_at,
            "params": {key: params[key] for key in sorted(params)},
        },
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


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
