from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.config import Settings
from app.data.database import Database
from app.data.models import BacktestRunModel, DensityEventModel, PaperProfileModel, PaperTradeModel
from app.data.repositories import (
    DensityRepository,
    HistoricalDataRepository,
    JobRepository,
    MLModelRepository,
)
from app.services.cache import AsyncTTLCache
from app.strategies.registry import default_registry

# density_events grows by ~20k rows/day, so unbounded aggregates scan the whole table.
DENSITY_SUMMARY_WINDOW_DAYS = 7


class StrategyLabService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.registry = default_registry(settings)
        self._cache: AsyncTTLCache[Any] = AsyncTTLCache(ttl_seconds=30.0)

    async def overview(self) -> dict[str, Any]:
        return {
            "strategies": await self.strategies(),
            "backtests": await self.backtest_runs(),
            "jobs": await self.jobs(),
            "coverage": await self.data_coverage(),
            "diagnostics": await self.diagnostics(),
            "instances": await self.instances(),
            "density_events": await self.density_events(),
            "density_summary": await self.density_summary(),
            "compare": await self.compare(),
            "ml_status": await self.ml_status(),
        }

    def invalidate_cache(self) -> None:
        self._cache.invalidate()

    async def section(self, name: str) -> dict[str, Any]:
        """Load only the data a single Strategy Lab tab renders."""
        if name == "strategies":
            return {
                "strategies": await self.strategies(),
                "profiles": await self.strategy_profiles(),
            }
        if name == "instances":
            return {"instances": await self.instances(), "strategies": await self.strategies()}
        if name == "backtests":
            return {"backtests": await self.backtest_runs(), "jobs": await self.jobs()}
        if name == "hyperopt":
            return {
                "instances": await self.instances(),
                "jobs": await self.jobs(),
                "ml_status": await self.ml_status(),
                "coverage": await self.data_coverage(),
            }
        if name == "compare":
            return {"compare": await self.compare(), "diagnostics": await self.diagnostics()}
        if name == "density":
            return {
                "density_events": await self.density_events(limit=60),
                "density_summary": await self.density_summary(),
            }
        return {
            "strategies": await self.strategies(),
            "instances": await self.instances(),
            "jobs": await self.jobs(),
            "compare": await self.compare(),
            "coverage": await self.data_coverage(),
        }

    async def strategy_profiles(self) -> list[dict[str, Any]]:
        return [
            {
                "key": profile_key,
                "enabled": profile.enabled,
                "strategies": profile.strategies,
                "min_score": profile.min_score,
                "symbols": profile.symbols,
                "paper_profile": profile.paper_profile,
            }
            for profile_key, profile in sorted(self.settings.strategy_profiles.profiles.items())
        ]

    async def strategies(self) -> list[dict[str, Any]]:
        return [
            {
                "key": item.key,
                "name": item.name,
                "enabled": item.enabled,
                "profiles": item.profiles,
                "instances": item.instances,
                "description": item.description,
            }
            for item in self.registry.descriptors(self.settings)
        ]

    async def instances(self) -> list[dict[str, Any]]:
        return [
            {
                "id": instance_id,
                "strategy_key": instance.strategy_key,
                "enabled": instance.enabled,
                "min_score": instance.min_score,
                "paper_profile": instance.paper_profile,
                "symbols": instance.symbols,
                "config": instance.config,
            }
            for instance_id, instance in sorted(self.settings.strategy_instances.instances.items())
        ]

    async def density_events(self, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            rows = await DensityRepository(session).recent_events(symbol=symbol, limit=limit)
        return [
            {
                "id": row.id,
                "timestamp": row.timestamp,
                "symbol": row.symbol,
                "side": row.side,
                "price": row.price,
                "size_usd": row.size_usd,
                "distance_pct": row.distance_pct,
                "lifetime_sec": row.lifetime_sec,
                "event_type": row.event_type,
                "absorption_score": row.absorption_score,
                "spoof_score": row.spoof_score,
            }
            for row in rows
        ]

    async def density_summary(self) -> list[dict[str, Any]]:
        return await self._cache.get_or_set("density_summary", self._density_summary)

    async def _density_summary(self) -> list[dict[str, Any]]:
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            days=DENSITY_SUMMARY_WINDOW_DAYS
        )
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    select(
                        DensityEventModel.symbol,
                        DensityEventModel.event_type,
                        func.count(DensityEventModel.id),
                        func.avg(DensityEventModel.size_usd),
                        func.avg(DensityEventModel.absorption_score),
                        func.avg(DensityEventModel.spoof_score),
                    )
                    .where(DensityEventModel.timestamp >= since)
                    .group_by(DensityEventModel.symbol, DensityEventModel.event_type)
                    .order_by(func.count(DensityEventModel.id).desc())
                    .limit(30)
                )
            ).all()
        return [
            {
                "symbol": symbol,
                "event_type": event_type,
                "events": int(count or 0),
                "avg_size_usd": float(avg_size or 0.0),
                "avg_absorption_score": float(avg_absorption or 0.0),
                "avg_spoof_score": float(avg_spoof or 0.0),
            }
            for symbol, event_type, count, avg_size, avg_absorption, avg_spoof in rows
        ]

    async def backtest_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            rows = list(
                (
                    await session.scalars(
                        select(BacktestRunModel)
                        .order_by(BacktestRunModel.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
        return [
            {
                "id": row.id,
                "created_at": row.created_at,
                "strategy_key": row.strategy_key,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "metrics": json.loads(row.metrics_json or "{}"),
                "status": row.status,
            }
            for row in rows
        ]

    async def data_coverage(self) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            return await HistoricalDataRepository(session).coverage()

    async def jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            jobs = await JobRepository(session).list_recent(limit)
        return [
            {
                "id": job.id,
                "job_type": job.job_type,
                "status": job.status,
                "created_at": job.created_at,
                "finished_at": job.finished_at,
                "error": job.error,
                "params": json.loads(job.params_json or "{}"),
                "result": json.loads(job.result_json or "{}") if job.result_json else {},
            }
            for job in jobs
        ]

    async def compare(self) -> dict[str, Any]:
        return await self._cache.get_or_set("compare", self._compare)

    async def _compare(self) -> dict[str, Any]:
        async with self.database.session() as session:
            profiles = list((await session.scalars(select(PaperProfileModel))).all())
            trades = list(
                (
                    await session.scalars(
                        select(PaperTradeModel).where(PaperTradeModel.status != "OPEN")
                    )
                ).all()
            )
            backtests = list(
                (
                    await session.scalars(
                        select(BacktestRunModel).order_by(BacktestRunModel.created_at.desc())
                    )
                ).all()
            )
        profile_rows = []
        by_profile = _bucket_trades(trades, "profile_key")
        for profile in profiles:
            stats = _trade_stats(by_profile.get(profile.profile_key, []))
            profile_rows.append(
                {
                    "profile_key": profile.profile_key,
                    "name": profile.name,
                    "enabled": profile.enabled,
                    "balance": profile.current_balance,
                    "equity": profile.equity,
                    "net_profit": profile.net_profit,
                    "max_drawdown_pct": profile.max_drawdown_pct,
                    **stats,
                }
            )
        backtest_rows = []
        latest_by_strategy: dict[str, BacktestRunModel] = {}
        for run in backtests:
            latest_by_strategy.setdefault(run.strategy_key, run)
        for strategy_key, run in latest_by_strategy.items():
            metrics = json.loads(run.metrics_json or "{}")
            backtest_rows.append(
                {
                    "strategy_key": strategy_key,
                    "symbol": run.symbol,
                    "timeframe": run.timeframe,
                    "created_at": run.created_at,
                    "total_trades": int(metrics.get("total_trades", 0) or 0),
                    "winrate": float(metrics.get("winrate", 0.0) or 0.0),
                    "profit_factor": float(metrics.get("profit_factor", 0.0) or 0.0),
                    "net_pnl": float(metrics.get("net_pnl", 0.0) or 0.0),
                    "max_drawdown_pct": float(metrics.get("max_drawdown", 0.0) or 0.0),
                }
            )
        return {"profiles": profile_rows, "backtests": backtest_rows}

    async def ml_status(self) -> dict[str, Any]:
        async with self.database.session() as session:
            active = await MLModelRepository(session).active()
        if active is None:
            return {"active": False, "reason": "no_active_model"}
        return {
            "active": True,
            "id": active.id,
            "model_type": active.model_type,
            "created_at": active.created_at,
            "model_path": active.model_path,
            "metrics": json.loads(active.metrics_json or "{}"),
        }

    async def diagnostics(self) -> dict[str, list[dict[str, Any]]]:
        return await self._cache.get_or_set("diagnostics", self._diagnostics)

    async def _diagnostics(self) -> dict[str, list[dict[str, Any]]]:
        async with self.database.session() as session:
            trades = list(
                (
                    await session.scalars(
                        select(PaperTradeModel).where(PaperTradeModel.status != "OPEN")
                    )
                ).all()
            )
        return {
            "by_strategy": _group_trade_pnl(trades, "strategy_key"),
            "by_instance": _group_trade_pnl(trades, "strategy_instance_id"),
            "by_profile": _group_trade_pnl(trades, "profile_key"),
            "by_pattern": _group_trade_pnl(trades, "pattern"),
            "by_symbol": _group_trade_pnl(trades, "symbol"),
            "by_score": _group_trade_pnl(trades, "score"),
            "by_hour": _group_trade_pnl(trades, "hour"),
            "by_status": _group_trade_pnl(trades, "status"),
        }


def _bucket_trades(trades: list[PaperTradeModel], field: str) -> dict[str, list[PaperTradeModel]]:
    buckets: dict[str, list[PaperTradeModel]] = defaultdict(list)
    for trade in trades:
        buckets[str(getattr(trade, field) or "unknown")].append(trade)
    return buckets


def _trade_stats(values: list[PaperTradeModel]) -> dict[str, Any]:
    wins = [trade for trade in values if trade.pnl_usd > 0]
    losses = [trade for trade in values if trade.pnl_usd < 0]
    gross_profit = sum(trade.pnl_usd for trade in wins)
    gross_loss = abs(sum(trade.pnl_usd for trade in losses))
    holding_minutes = [
        (trade.closed_at - trade.opened_at).total_seconds() / 60
        for trade in values
        if trade.closed_at is not None
    ]
    return {
        "trades": len(values),
        "net_pnl": sum(trade.pnl_usd for trade in values),
        "winrate": len(wins) / len(values) * 100 if values else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else gross_profit,
        "expectancy_r": (
            sum(trade.realized_rr for trade in values) / len(values) if values else 0.0
        ),
        "avg_trade": sum(trade.pnl_usd for trade in values) / len(values) if values else 0.0,
        "avg_holding_minutes": (
            sum(holding_minutes) / len(holding_minutes) if holding_minutes else 0.0
        ),
        "tp_rate": _status_rate(values, "CLOSED_TP"),
        "sl_rate": _status_rate(values, "CLOSED_SL"),
        "timeout_rate": _status_rate(values, "EXPIRED"),
    }


def _group_trade_pnl(trades: list[PaperTradeModel], field: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[PaperTradeModel]] = defaultdict(list)
    for trade in trades:
        if field == "hour":
            key = f"{trade.opened_at.hour:02d}:00"
        elif field == "score":
            key = f"{int(trade.score)}"
        else:
            key = str(getattr(trade, field) or "unknown")
        buckets[key].append(trade)
    rows = []
    for key, values in buckets.items():
        stats = _trade_stats(values)
        rows.append(
            {
                "key": key,
                **stats,
                "avg_mfe": 0.0,
                "avg_mae": 0.0,
            }
        )
    rows.sort(key=lambda item: item["net_pnl"])
    return rows[:20]


def _status_rate(trades: list[PaperTradeModel], status: str) -> float:
    if not trades:
        return 0.0
    return len([trade for trade in trades if trade.status == status]) / len(trades) * 100
