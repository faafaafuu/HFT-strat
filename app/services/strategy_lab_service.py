from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from app.config import Settings
from app.data.database import Database
from app.data.models import BacktestRunModel, PaperTradeModel
from app.data.repositories import HistoricalDataRepository, JobRepository
from app.strategies.registry import default_registry


class StrategyLabService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.registry = default_registry(settings)

    async def overview(self) -> dict[str, Any]:
        return {
            "strategies": await self.strategies(),
            "backtests": await self.backtest_runs(),
            "jobs": await self.jobs(),
            "coverage": await self.data_coverage(),
            "diagnostics": await self.diagnostics(),
        }

    async def strategies(self) -> list[dict[str, Any]]:
        return [
            {
                "key": item.key,
                "name": item.name,
                "enabled": item.enabled,
                "profiles": item.profiles,
            }
            for item in self.registry.descriptors(self.settings)
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
            }
            for job in jobs
        ]

    async def diagnostics(self) -> dict[str, list[dict[str, Any]]]:
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
            "by_pattern": _group_trade_pnl(trades, "pattern"),
            "by_symbol": _group_trade_pnl(trades, "symbol"),
            "by_score": _group_trade_pnl(trades, "score"),
            "by_hour": _group_trade_pnl(trades, "hour"),
        }


def _group_trade_pnl(trades: list[PaperTradeModel], field: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[PaperTradeModel]] = defaultdict(list)
    for trade in trades:
        if field == "hour":
            key = f"{trade.opened_at.hour:02d}:00"
        else:
            key = str(getattr(trade, field) or "unknown")
        buckets[key].append(trade)
    rows = []
    for key, values in buckets.items():
        wins = [trade for trade in values if trade.pnl_usd > 0]
        losses = [trade for trade in values if trade.pnl_usd < 0]
        gross_profit = sum(trade.pnl_usd for trade in wins)
        gross_loss = abs(sum(trade.pnl_usd for trade in losses))
        rows.append(
            {
                "key": key,
                "trades": len(values),
                "net_pnl": sum(trade.pnl_usd for trade in values),
                "winrate": len(wins) / len(values) * 100 if values else 0.0,
                "profit_factor": gross_profit / gross_loss if gross_loss else gross_profit,
                "avg_mfe": 0.0,
                "avg_mae": 0.0,
            }
        )
    rows.sort(key=lambda item: item["net_pnl"])
    return rows[:20]
