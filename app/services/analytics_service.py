from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from app.data.database import Database
from app.data.models import PaperTradeModel, SignalModel, SignalOutcomeModel
from app.data.repositories import SignalRepository
from app.paper.statistics import paper_profiles_summary
from app.services.cache import AsyncTTLCache
from app.services.serializers import normalize_signal_summary


class AnalyticsService:
    def __init__(self, database: Database, cache_ttl_seconds: float = 10.0) -> None:
        self.database = database
        self.cache: AsyncTTLCache[dict[str, Any]] = AsyncTTLCache(cache_ttl_seconds)

    async def summary(self) -> dict[str, Any]:
        return await self.cache.get_or_set("summary", self._summary_uncached)

    async def _summary_uncached(self) -> dict[str, Any]:
        async with self.database.session() as session:
            signal_summary = normalize_signal_summary(await SignalRepository(session).summary())
            profiles = await paper_profiles_summary(session)
            trades = list(
                (
                    await session.scalars(
                        select(PaperTradeModel).where(PaperTradeModel.status != "OPEN")
                    )
                ).all()
            )
            outcomes = list(
                (
                    await session.execute(
                        select(SignalModel, SignalOutcomeModel)
                        .join(SignalOutcomeModel, SignalOutcomeModel.signal_id == SignalModel.id)
                        .where(SignalOutcomeModel.horizon_minutes == 30)
                    )
                ).all()
            )
        return {
            "signals": signal_summary,
            "profiles": profiles,
            "paper": _paper_aggregate(trades),
            "by_symbol": _outcome_group(outcomes, "symbol"),
            "by_pattern": _outcome_group(outcomes, "pattern"),
            "by_score": _outcome_group(outcomes, "score"),
            "by_hour": _outcome_group(outcomes, "hour"),
        }


def _paper_aggregate(trades: list[PaperTradeModel]) -> dict[str, Any]:
    wins = [trade for trade in trades if trade.pnl_usd > 0]
    losses = [trade for trade in trades if trade.pnl_usd < 0]
    gross_profit = sum(trade.pnl_usd for trade in wins)
    gross_loss = abs(sum(trade.pnl_usd for trade in losses))
    total = len(trades)
    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "winrate": len(wins) / total * 100 if total else 0.0,
        "net_pnl": sum(trade.pnl_usd for trade in trades),
        "profit_factor": (
            gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0)
        ),
        "expectancy_r": sum(trade.realized_rr for trade in trades) / total if total else 0.0,
        "average_trade": sum(trade.pnl_usd for trade in trades) / total if total else 0.0,
    }


def _outcome_group(rows: list[tuple[SignalModel, SignalOutcomeModel]], group: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[SignalOutcomeModel]] = defaultdict(list)
    for signal, outcome in rows:
        if group == "hour":
            key = f"{signal.timestamp.hour:02d}:00"
        else:
            key = str(getattr(signal, group))
        buckets[key].append(outcome)
    result = []
    for key, values in buckets.items():
        total = len(values)
        wins = sum(1 for item in values if item.hit_tp_1_0)
        result.append(
            {
                "key": key,
                "count": total,
                "winrate": wins / total * 100 if total else 0.0,
                "avg_mfe": sum(item.mfe_pct for item in values) / total if total else 0.0,
                "avg_mae": sum(item.mae_pct for item in values) / total if total else 0.0,
            }
        )
    result.sort(key=lambda item: (item["winrate"], item["count"]), reverse=True)
    return result[:20]
