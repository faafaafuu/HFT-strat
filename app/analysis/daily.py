from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.data.database import Database
from app.data.models import PaperTradeModel, SignalModel, SignalOutcomeModel
from app.data.repositories import StrategyAnalysisRepository
from app.logger import get_logger
from app.utils.time import utc_now


class DailyStrategyAnalysisJob:
    def __init__(self, database: Database, interval_hours: int = 24) -> None:
        self.database = database
        self.interval_seconds = interval_hours * 3600
        self.log = get_logger("strategy_analysis")
        self._stop = asyncio.Event()

    async def run(self) -> None:
        await self.run_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                await self.run_once()

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> None:
        now = utc_now()
        period_end = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        period_start = period_end - timedelta(days=1)
        async with self.database.session() as session:
            trades = list(
                (
                    await session.scalars(
                        select(PaperTradeModel).where(
                            PaperTradeModel.status != "OPEN",
                            PaperTradeModel.closed_at >= period_start,
                            PaperTradeModel.closed_at <= period_end,
                        )
                    )
                ).all()
            )
            signal_ids = [trade.signal_id for trade in trades if trade.signal_id is not None]
            outcome_rows = []
            if signal_ids:
                outcome_rows = list(
                    (
                        await session.execute(
                            select(SignalModel, SignalOutcomeModel)
                            .join(
                                SignalOutcomeModel,
                                SignalOutcomeModel.signal_id == SignalModel.id,
                            )
                            .where(
                                SignalModel.id.in_(signal_ids),
                                SignalOutcomeModel.horizon_minutes == 30,
                            )
                        )
                    ).all()
                )
            outcome_by_signal = {
                signal.id: outcome for signal, outcome in outcome_rows if signal.id is not None
            }
            rows = _analysis_rows(trades, outcome_by_signal, period_start, period_end)
            await StrategyAnalysisRepository(session).replace_period(period_start, period_end, rows)
        self.log.info("strategy analysis saved rows=%s", len(rows))


def _analysis_rows(
    trades: list[PaperTradeModel],
    outcome_by_signal: dict[int, SignalOutcomeModel],
    period_start: datetime,
    period_end: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    rows.extend(_group_rows(trades, outcome_by_signal, "profile", period_start, period_end))
    rows.extend(_group_rows(trades, outcome_by_signal, "pattern", period_start, period_end))
    rows.extend(_group_rows(trades, outcome_by_signal, "symbol", period_start, period_end))
    rows.extend(_group_rows(trades, outcome_by_signal, "hour", period_start, period_end))
    rows.extend(_group_rows(trades, outcome_by_signal, "score", period_start, period_end))
    return rows


def _group_rows(
    trades: list[PaperTradeModel],
    outcome_by_signal: dict[int, SignalOutcomeModel],
    group: str,
    period_start: datetime,
    period_end: datetime,
) -> Iterable[dict[str, object]]:
    buckets: dict[str, list[PaperTradeModel]] = {}
    for trade in trades:
        key = _bucket_key(trade, group)
        buckets.setdefault(key, []).append(trade)
    for key, bucket in buckets.items():
        wins = [trade for trade in bucket if trade.pnl_usd > 0]
        losses = [trade for trade in bucket if trade.pnl_usd < 0]
        gross_profit = sum(trade.pnl_usd for trade in wins)
        gross_loss = abs(sum(trade.pnl_usd for trade in losses))
        outcomes = [
            outcome_by_signal[trade.signal_id]
            for trade in bucket
            if trade.signal_id in outcome_by_signal
        ]
        total = len(bucket)
        profile_key = key if group == "profile" else None
        pattern = key if group in {"pattern", "hour", "score"} else None
        symbol = key if group == "symbol" else None
        expectancy = sum(trade.realized_rr for trade in bucket) / total if total else 0.0
        yield {
            "profile_key": profile_key,
            "pattern": pattern,
            "symbol": symbol,
            "total_trades": total,
            "winrate": len(wins) / total * 100 if total else 0.0,
            "profit_factor": (
                gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0)
            ),
            "expectancy": expectancy,
            "avg_mfe": (
                sum(outcome.mfe_pct for outcome in outcomes) / len(outcomes) if outcomes else 0.0
            ),
            "avg_mae": (
                sum(outcome.mae_pct for outcome in outcomes) / len(outcomes) if outcomes else 0.0
            ),
            "conclusion": {
                "group": group,
                "key": key,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "net_pnl": sum(trade.pnl_usd for trade in bucket),
            },
        }


def _bucket_key(trade: PaperTradeModel, group: str) -> str:
    if group == "profile":
        return trade.profile_key
    if group == "pattern":
        return trade.pattern or "n/a"
    if group == "symbol":
        return trade.symbol
    if group == "hour":
        opened = (
            trade.opened_at.replace(tzinfo=UTC)
            if trade.opened_at.tzinfo is None
            else trade.opened_at
        )
        return f"hour:{opened.astimezone(UTC).hour:02d}"
    if group == "score":
        return f"score:{trade.score}"
    return "all"
