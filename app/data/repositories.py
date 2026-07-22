from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import (
    BacktestEquityCurveModel,
    BacktestRunModel,
    BacktestTradeModel,
    DensityEventModel,
    DensityLevelModel,
    HistoricalCandleModel,
    HyperoptEvaluationModel,
    JobModel,
    MarketSnapshotModel,
    MLModelRunModel,
    OrderbookEventModel,
    RuntimeSettingModel,
    SignalModel,
    SignalOutcomeModel,
    StrategyAnalysisModel,
    SymbolModel,
)
from app.utils.time import utc_now


class MarketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_symbol(
        self,
        exchange: str,
        symbol: str,
        base: str | None,
        quote: str | None,
        is_active: bool,
        volume_24h_usd: float | None,
        spread_pct: float | None,
        depth_1pct_usd: float | None,
    ) -> None:
        stmt = sqlite_insert(SymbolModel).values(
            exchange=exchange,
            symbol=symbol,
            base=base,
            quote=quote,
            is_active=is_active,
            volume_24h_usd=volume_24h_usd,
            spread_pct=spread_pct,
            depth_1pct_usd=depth_1pct_usd,
            updated_at=utc_now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["exchange", "symbol"],
            set_={
                "base": base,
                "quote": quote,
                "is_active": is_active,
                "volume_24h_usd": volume_24h_usd,
                "spread_pct": spread_pct,
                "depth_1pct_usd": depth_1pct_usd,
                "updated_at": utc_now(),
            },
        )
        await self.session.execute(stmt)

    async def add_market_snapshot(
        self,
        exchange: str,
        symbol: str,
        timestamp: datetime,
        price: float,
        volume_1m: float | None,
        volume_5m: float | None,
        oi: float | None,
        oi_change_5m: float | None,
        oi_change_15m: float | None,
        funding_rate: float | None,
        spread_pct: float | None,
        bid_depth_1pct: float | None,
        ask_depth_1pct: float | None,
    ) -> None:
        self.session.add(
            MarketSnapshotModel(
                exchange=exchange,
                symbol=symbol,
                timestamp=timestamp,
                price=price,
                volume_1m=volume_1m,
                volume_5m=volume_5m,
                oi=oi,
                oi_change_5m=oi_change_5m,
                oi_change_15m=oi_change_15m,
                funding_rate=funding_rate,
                spread_pct=spread_pct,
                bid_depth_1pct=bid_depth_1pct,
                ask_depth_1pct=ask_depth_1pct,
            )
        )

    async def purge_old_market_data(
        self,
        market_snapshot_cutoff: datetime,
        orderbook_event_cutoff: datetime,
    ) -> None:
        await self.session.execute(
            delete(MarketSnapshotModel).where(
                MarketSnapshotModel.timestamp < market_snapshot_cutoff
            )
        )
        await self.session.execute(
            delete(OrderbookEventModel).where(
                OrderbookEventModel.timestamp < orderbook_event_cutoff
            )
        )

    async def min_max_price_since(
        self,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> tuple[float | None, float | None, float | None]:
        last_stmt = (
            select(MarketSnapshotModel.price)
            .where(
                MarketSnapshotModel.exchange == exchange,
                MarketSnapshotModel.symbol == symbol,
                MarketSnapshotModel.timestamp >= start,
                MarketSnapshotModel.timestamp <= end,
            )
            .order_by(MarketSnapshotModel.timestamp.desc())
            .limit(1)
        )
        agg_stmt = select(
            func.min(MarketSnapshotModel.price),
            func.max(MarketSnapshotModel.price),
        ).where(
            MarketSnapshotModel.exchange == exchange,
            MarketSnapshotModel.symbol == symbol,
            MarketSnapshotModel.timestamp >= start,
            MarketSnapshotModel.timestamp <= end,
        )
        price_after = await self.session.scalar(last_stmt)
        min_price, max_price = (await self.session.execute(agg_stmt)).one()
        if price_after is None or min_price is None or max_price is None:
            return None, None, None
        return float(price_after), float(min_price), float(max_price)


class RuntimeSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all(self) -> dict[str, Any]:
        rows = list((await self.session.scalars(select(RuntimeSettingModel))).all())
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[row.key] = json.loads(row.value_json)
            except json.JSONDecodeError:
                continue
        return result

    async def set(self, key: str, value: Any) -> None:
        stmt = sqlite_insert(RuntimeSettingModel).values(
            key=key,
            value_json=json.dumps(value, ensure_ascii=False, default=str),
            updated_at=utc_now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={
                "value_json": json.dumps(value, ensure_ascii=False, default=str),
                "updated_at": utc_now(),
            },
        )
        await self.session.execute(stmt)


class StrategyAnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_period(
        self,
        period_start: datetime,
        period_end: datetime,
        rows: list[dict[str, Any]],
    ) -> None:
        await self.session.execute(
            delete(StrategyAnalysisModel).where(
                StrategyAnalysisModel.period_start == period_start,
                StrategyAnalysisModel.period_end == period_end,
            )
        )
        for row in rows:
            self.session.add(
                StrategyAnalysisModel(
                    created_at=utc_now(),
                    period_start=period_start,
                    period_end=period_end,
                    profile_key=row.get("profile_key"),
                    pattern=row.get("pattern"),
                    symbol=row.get("symbol"),
                    total_trades=int(row.get("total_trades", 0)),
                    winrate=float(row.get("winrate", 0.0)),
                    profit_factor=float(row.get("profit_factor", 0.0)),
                    expectancy=float(row.get("expectancy", 0.0)),
                    avg_mfe=float(row.get("avg_mfe", 0.0)),
                    avg_mae=float(row.get("avg_mae", 0.0)),
                    conclusion_json=json.dumps(
                        row.get("conclusion", {}), ensure_ascii=False, default=str
                    ),
                )
            )

    async def add_orderbook_event(
        self,
        exchange: str,
        symbol: str,
        timestamp: datetime,
        event_type: str,
        side: str | None,
        price: float | None,
        size_usd: float | None,
        distance_pct: float | None,
        lifetime_sec: float | None,
    ) -> None:
        self.session.add(
            OrderbookEventModel(
                exchange=exchange,
                symbol=symbol,
                timestamp=timestamp,
                event_type=event_type,
                side=side,
                price=price,
                size_usd=size_usd,
                distance_pct=distance_pct,
                lifetime_sec=lifetime_sec,
            )
        )


class SignalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_signal(
        self,
        exchange: str,
        symbol: str,
        timestamp: datetime,
        direction: str,
        pattern: str,
        score: int,
        entry_price: float,
        reasons: list[str],
        market_context: dict[str, Any],
        strategy_key: str | None = None,
        strategy_instance_id: str | None = None,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
        invalidation_level: float | None = None,
        suggested_stop_pct: float | None = None,
        suggested_take_pct: float | None = None,
        confidence: float | None = None,
        ml_signal_quality_score: float | None = None,
    ) -> SignalModel:
        signal = SignalModel(
            exchange=exchange,
            symbol=symbol,
            timestamp=timestamp,
            direction=direction,
            pattern=pattern,
            strategy_key=strategy_key,
            strategy_instance_id=strategy_instance_id,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
            score=score,
            entry_price=entry_price,
            invalidation_level=invalidation_level,
            suggested_stop_pct=suggested_stop_pct,
            suggested_take_pct=suggested_take_pct,
            confidence=confidence,
            ml_signal_quality_score=ml_signal_quality_score,
            reasons_json=json.dumps(reasons, ensure_ascii=False),
            market_context_json=json.dumps(market_context, ensure_ascii=False, default=str),
            status="open",
        )
        self.session.add(signal)
        await self.session.flush()
        return signal

    async def latest_signal_for_symbol(
        self,
        exchange: str,
        symbol: str,
        since: datetime,
        pattern: str | None = None,
    ) -> SignalModel | None:
        filters = [
            SignalModel.exchange == exchange,
            SignalModel.symbol == symbol,
            SignalModel.timestamp >= since,
        ]
        if pattern is not None:
            filters.append(SignalModel.pattern == pattern)
        stmt = (
            select(SignalModel)
            .where(and_(*filters))
            .order_by(SignalModel.timestamp.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def get_signal(self, signal_id: int) -> SignalModel | None:
        return await self.session.get(SignalModel, signal_id)

    async def list_signals_missing_outcomes(
        self,
        horizons: list[int],
        now: datetime | None = None,
    ) -> list[SignalModel]:
        now = now or utc_now()
        if not horizons:
            return []
        min_timestamp = now - timedelta(minutes=max(horizons) + 30)
        stmt = (
            select(SignalModel)
            .where(SignalModel.timestamp >= min_timestamp)
            .order_by(SignalModel.timestamp.asc())
        )
        signals = list((await self.session.scalars(stmt)).all())
        result: list[SignalModel] = []
        for signal in signals:
            signal_ts = _aware(signal.timestamp)
            due = [h for h in horizons if signal_ts + timedelta(minutes=h) <= now]
            if not due:
                continue
            existing_stmt = select(SignalOutcomeModel.horizon_minutes).where(
                SignalOutcomeModel.signal_id == signal.id
            )
            existing = set((await self.session.scalars(existing_stmt)).all())
            if any(h not in existing for h in due):
                result.append(signal)
        return result

    async def add_outcome(
        self,
        signal_id: int,
        horizon_minutes: int,
        price_after: float,
        mfe_pct: float,
        mae_pct: float,
        hits: dict[str, bool],
    ) -> None:
        stmt = sqlite_insert(SignalOutcomeModel).values(
            signal_id=signal_id,
            horizon_minutes=horizon_minutes,
            price_after=price_after,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            hit_tp_0_5=hits.get("tp_0_5", False),
            hit_tp_1_0=hits.get("tp_1_0", False),
            hit_tp_1_5=hits.get("tp_1_5", False),
            hit_sl_0_3=hits.get("sl_0_3", False),
            hit_sl_0_5=hits.get("sl_0_5", False),
            hit_sl_0_7=hits.get("sl_0_7", False),
            created_at=utc_now(),
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["signal_id", "horizon_minutes"])
        await self.session.execute(stmt)

    async def summary(self, since: datetime | None = None) -> dict[str, Any]:
        signal_filters = []
        outcome_filters = []
        if since is not None:
            signal_filters.append(SignalModel.timestamp >= since)
            outcome_filters.append(SignalModel.timestamp >= since)

        total = await self.session.scalar(select(func.count(SignalModel.id)).where(*signal_filters))
        outcome_stmt = (
            select(
                func.avg(SignalOutcomeModel.mfe_pct),
                func.avg(SignalOutcomeModel.mae_pct),
                func.avg(SignalOutcomeModel.hit_tp_1_0),
                func.count(SignalOutcomeModel.id),
            )
            .join(SignalModel, SignalModel.id == SignalOutcomeModel.signal_id)
            .where(SignalOutcomeModel.horizon_minutes == 30, *outcome_filters)
        )
        avg_mfe, avg_mae, winrate_tp1, outcome_count = (
            await self.session.execute(outcome_stmt)
        ).one()

        best_pattern_stmt = (
            select(SignalModel.pattern, func.avg(SignalOutcomeModel.hit_tp_1_0).label("wr"))
            .join(SignalOutcomeModel, SignalOutcomeModel.signal_id == SignalModel.id)
            .where(SignalOutcomeModel.horizon_minutes == 30, *signal_filters)
            .group_by(SignalModel.pattern)
            .order_by(func.avg(SignalOutcomeModel.hit_tp_1_0).desc())
            .limit(1)
        )
        best_pattern = (await self.session.execute(best_pattern_stmt)).first()

        best_pair_stmt = (
            select(SignalModel.symbol, func.avg(SignalOutcomeModel.hit_tp_1_0).label("wr"))
            .join(SignalOutcomeModel, SignalOutcomeModel.signal_id == SignalModel.id)
            .where(SignalOutcomeModel.horizon_minutes == 30, *signal_filters)
            .group_by(SignalModel.symbol)
            .order_by(func.avg(SignalOutcomeModel.hit_tp_1_0).desc())
            .limit(1)
        )
        best_pair = (await self.session.execute(best_pair_stmt)).first()

        worst_pair_stmt = (
            select(SignalModel.symbol, func.avg(SignalOutcomeModel.hit_tp_1_0).label("wr"))
            .join(SignalOutcomeModel, SignalOutcomeModel.signal_id == SignalModel.id)
            .where(SignalOutcomeModel.horizon_minutes == 30, *signal_filters)
            .group_by(SignalModel.symbol)
            .order_by(func.avg(SignalOutcomeModel.hit_tp_1_0).asc())
            .limit(1)
        )
        worst_pair = (await self.session.execute(worst_pair_stmt)).first()

        return {
            "total_signals": int(total or 0),
            "outcome_count_30m": int(outcome_count or 0),
            "avg_mfe_30m": float(avg_mfe or 0),
            "avg_mae_30m": float(avg_mae or 0),
            "winrate_tp1_30m": float(winrate_tp1 or 0) * 100,
            "best_pattern": best_pattern,
            "best_pair": best_pair,
            "worst_pair": worst_pair,
        }

    async def count_since(self, since: datetime) -> int:
        total = await self.session.scalar(
            select(func.count(SignalModel.id)).where(SignalModel.timestamp >= since)
        )
        return int(total or 0)

    async def list_recent(self, limit: int = 10, offset: int = 0) -> list[SignalModel]:
        stmt = (
            select(SignalModel).order_by(SignalModel.timestamp.desc()).limit(limit).offset(offset)
        )
        return list((await self.session.scalars(stmt)).all())

    async def get_signal_with_outcomes(self, signal_id: int) -> SignalModel | None:
        stmt = (
            select(SignalModel)
            .where(SignalModel.id == signal_id)
            .options(selectinload(SignalModel.outcomes))
        )
        return await self.session.scalar(stmt)

    async def mark_entered_manual(
        self,
        signal_id: int,
        entry_price: float,
        entered_at: datetime,
    ) -> SignalModel | None:
        signal = await self.session.get(SignalModel, signal_id)
        if signal is None:
            return None
        signal.status = "ENTERED_MANUAL"
        signal.manual_entry_price = entry_price
        signal.manual_entered_at = entered_at
        return signal

    async def ignore_signal(self, signal_id: int) -> SignalModel | None:
        signal = await self.session.get(SignalModel, signal_id)
        if signal is None:
            return None
        signal.status = "IGNORED"
        return signal

    async def last_signal_time(self) -> datetime | None:
        stmt = select(SignalModel.timestamp).order_by(SignalModel.timestamp.desc()).limit(1)
        return await self.session.scalar(stmt)

    async def top_pairs(self, limit: int = 10) -> list[tuple[str, float, int]]:
        stmt = (
            select(
                SignalModel.symbol,
                func.avg(SignalOutcomeModel.hit_tp_1_0).label("wr"),
                func.count(SignalModel.id).label("cnt"),
            )
            .join(SignalOutcomeModel, SignalOutcomeModel.signal_id == SignalModel.id)
            .where(SignalOutcomeModel.horizon_minutes == 30)
            .group_by(SignalModel.symbol)
            .order_by(func.avg(SignalOutcomeModel.hit_tp_1_0).desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(str(symbol), float(wr or 0) * 100, int(cnt or 0)) for symbol, wr, cnt in rows]

    async def top_patterns(self, limit: int = 10) -> list[tuple[str, float, int]]:
        stmt = (
            select(
                SignalModel.pattern,
                func.avg(SignalOutcomeModel.hit_tp_1_0).label("wr"),
                func.count(SignalModel.id).label("cnt"),
            )
            .join(SignalOutcomeModel, SignalOutcomeModel.signal_id == SignalModel.id)
            .where(SignalOutcomeModel.horizon_minutes == 30)
            .group_by(SignalModel.pattern)
            .order_by(func.avg(SignalOutcomeModel.hit_tp_1_0).desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(str(pattern), float(wr or 0) * 100, int(cnt or 0)) for pattern, wr, cnt in rows]


class HistoricalDataRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_candles(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        for row in rows:
            stmt = sqlite_insert(HistoricalCandleModel).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=["exchange", "symbol", "timeframe", "open_time"],
                set_={
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row.get("volume", 0.0),
                    "turnover": row.get("turnover", 0.0),
                },
            )
            await self.session.execute(stmt)
        return len(rows)

    async def candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[HistoricalCandleModel]:
        filters = [
            HistoricalCandleModel.exchange == exchange,
            HistoricalCandleModel.symbol == symbol,
            HistoricalCandleModel.timeframe == timeframe,
        ]
        if since is not None:
            filters.append(HistoricalCandleModel.open_time >= since)
        stmt = select(HistoricalCandleModel).where(*filters).order_by(
            HistoricalCandleModel.open_time.asc()
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.session.scalars(stmt)).all())

    async def coverage(self) -> list[dict[str, Any]]:
        stmt = (
            select(
                HistoricalCandleModel.exchange,
                HistoricalCandleModel.symbol,
                HistoricalCandleModel.timeframe,
                func.min(HistoricalCandleModel.open_time),
                func.max(HistoricalCandleModel.open_time),
                func.count(HistoricalCandleModel.id),
            )
            .group_by(
                HistoricalCandleModel.exchange,
                HistoricalCandleModel.symbol,
                HistoricalCandleModel.timeframe,
            )
            .order_by(HistoricalCandleModel.symbol.asc(), HistoricalCandleModel.timeframe.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "candles": int(count or 0),
            }
            for exchange, symbol, timeframe, start, end, count in rows
        ]


class BacktestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(
        self,
        strategy_key: str,
        symbol: str,
        timeframe: str,
        period_start: datetime | None,
        period_end: datetime | None,
        params: dict[str, Any],
        metrics: dict[str, Any],
        trades: list[dict[str, Any]],
        equity_curve: list[dict[str, Any]],
    ) -> BacktestRunModel:
        run = BacktestRunModel(
            strategy_key=strategy_key,
            symbol=symbol,
            timeframe=timeframe,
            period_start=period_start,
            period_end=period_end,
            params_json=json.dumps(params, ensure_ascii=False, default=str),
            metrics_json=json.dumps(metrics, ensure_ascii=False, default=str),
            status="DONE",
        )
        self.session.add(run)
        await self.session.flush()
        for row in trades:
            self.session.add(BacktestTradeModel(run_id=run.id, **row))
        for row in equity_curve:
            self.session.add(BacktestEquityCurveModel(run_id=run.id, **row))
        return run

    async def recent_runs(self, limit: int = 20) -> list[BacktestRunModel]:
        stmt = select(BacktestRunModel).order_by(BacktestRunModel.created_at.desc()).limit(limit)
        return list((await self.session.scalars(stmt)).all())


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, job_type: str, params: dict[str, Any]) -> JobModel:
        job = JobModel(
            job_type=job_type,
            status="PENDING",
            params_json=json.dumps(params, ensure_ascii=False, default=str),
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def list_recent(self, limit: int = 20) -> list[JobModel]:
        stmt = select(JobModel).order_by(JobModel.created_at.desc()).limit(limit)
        return list((await self.session.scalars(stmt)).all())


class HyperoptCacheRepository:
    """Stores evaluated parameter combinations so repeat sweeps skip the work."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_many(self, cache_keys: list[str]) -> dict[str, dict[str, Any]]:
        if not cache_keys:
            return {}
        rows = list(
            (
                await self.session.scalars(
                    select(HyperoptEvaluationModel).where(
                        HyperoptEvaluationModel.cache_key.in_(cache_keys)
                    )
                )
            ).all()
        )
        found = {}
        for row in rows:
            row.hits += 1
            row.last_used_at = utc_now()
            found[row.cache_key] = {
                "params": json.loads(row.params_json),
                "timeframe": row.timeframe,
                "objective": row.objective,
                "train": json.loads(row.train_json or "{}"),
                "test": json.loads(row.test_json or "{}"),
                "cached": True,
            }
        return found

    async def store(
        self,
        *,
        cache_key: str,
        strategy_key: str,
        symbol: str,
        timeframe: str,
        params: dict[str, Any],
        period_start: datetime,
        period_end: datetime,
        objective: float,
        train: dict[str, Any],
        test: dict[str, Any],
    ) -> None:
        values = {
            "cache_key": cache_key,
            "strategy_key": strategy_key,
            "symbol": symbol,
            "timeframe": timeframe,
            "params_json": json.dumps(params, ensure_ascii=False, sort_keys=True, default=str),
            "period_start": period_start,
            "period_end": period_end,
            "objective": objective,
            "train_json": json.dumps(train, ensure_ascii=False, default=str),
            "test_json": json.dumps(test, ensure_ascii=False, default=str),
            "created_at": utc_now(),
            "last_used_at": utc_now(),
        }
        stmt = sqlite_insert(HyperoptEvaluationModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "objective": values["objective"],
                "train_json": values["train_json"],
                "test_json": values["test_json"],
                "last_used_at": values["last_used_at"],
            },
        )
        await self.session.execute(stmt)

    async def stats(self) -> dict[str, Any]:
        rows = (
            await self.session.execute(
                select(
                    HyperoptEvaluationModel.strategy_key,
                    HyperoptEvaluationModel.symbol,
                    HyperoptEvaluationModel.timeframe,
                    func.count(HyperoptEvaluationModel.id),
                    func.sum(HyperoptEvaluationModel.hits),
                    func.max(HyperoptEvaluationModel.last_used_at),
                )
                .group_by(
                    HyperoptEvaluationModel.strategy_key,
                    HyperoptEvaluationModel.symbol,
                    HyperoptEvaluationModel.timeframe,
                )
                .order_by(func.count(HyperoptEvaluationModel.id).desc())
                .limit(30)
            )
        ).all()
        total = await self.session.scalar(select(func.count(HyperoptEvaluationModel.id)))
        saved = await self.session.scalar(select(func.sum(HyperoptEvaluationModel.hits)))
        return {
            "total": int(total or 0),
            "reused": int(saved or 0),
            "rows": [
                {
                    "strategy_key": strategy_key,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "combinations": int(count or 0),
                    "reused": int(hits or 0),
                    "last_used_at": last_used,
                }
                for strategy_key, symbol, timeframe, count, hits, last_used in rows
            ],
        }

    async def clear(self, strategy_key: str | None = None) -> int:
        stmt = delete(HyperoptEvaluationModel)
        if strategy_key:
            stmt = stmt.where(HyperoptEvaluationModel.strategy_key == strategy_key)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)


class DensityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_events(self, events) -> int:
        count = 0
        for event in events:
            self.session.add(
                DensityEventModel(
                    exchange=event.exchange,
                    symbol=event.symbol,
                    timestamp=event.timestamp,
                    side=event.side,
                    price=event.price,
                    size_usd=event.size_usd,
                    distance_pct=event.distance_pct,
                    lifetime_sec=event.lifetime_sec,
                    event_type=event.event_type,
                    pulled_pct=event.pulled_pct,
                    eaten_pct=event.eaten_pct,
                    refill_count=event.refill_count,
                    absorption_score=event.absorption_score,
                    spoof_score=event.spoof_score,
                    context_json=json.dumps(event.context or {}, ensure_ascii=False, default=str),
                )
            )
            count += 1
        return count

    async def upsert_levels(self, levels) -> int:
        count = 0
        for level in levels:
            values = {
                "exchange": level.exchange,
                "symbol": level.symbol,
                "side": level.side,
                "price": level.price,
                "first_seen_at": level.first_seen_at,
                "last_seen_at": level.last_seen_at,
                "max_size_usd": level.max_size_usd,
                "current_size_usd": level.current_size_usd,
                "lifetime_sec": level.lifetime_sec,
                "status": level.status,
                "stats_json": json.dumps(level.stats or {}, ensure_ascii=False, default=str),
            }
            stmt = sqlite_insert(DensityLevelModel).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["exchange", "symbol", "side", "price"],
                set_={
                    "last_seen_at": values["last_seen_at"],
                    "max_size_usd": values["max_size_usd"],
                    "current_size_usd": values["current_size_usd"],
                    "lifetime_sec": values["lifetime_sec"],
                    "status": values["status"],
                    "stats_json": values["stats_json"],
                },
            )
            await self.session.execute(stmt)
            count += 1
        return count

    async def recent_events(
        self,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[DensityEventModel]:
        filters = []
        if symbol:
            filters.append(DensityEventModel.symbol == symbol)
        stmt = (
            select(DensityEventModel)
            .where(*filters)
            .order_by(DensityEventModel.timestamp.desc())
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())


class MLModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_run(
        self,
        model_type: str,
        features: list[str],
        metrics: dict[str, Any],
        model_path: str,
        is_active: bool,
        train_period_start: datetime | None = None,
        train_period_end: datetime | None = None,
        test_period_start: datetime | None = None,
        test_period_end: datetime | None = None,
    ) -> MLModelRunModel:
        if is_active:
            rows = list((await self.session.scalars(select(MLModelRunModel))).all())
            for row in rows:
                row.is_active = False
        run = MLModelRunModel(
            model_type=model_type,
            train_period_start=train_period_start,
            train_period_end=train_period_end,
            test_period_start=test_period_start,
            test_period_end=test_period_end,
            features_json=json.dumps(features, ensure_ascii=False),
            metrics_json=json.dumps(metrics, ensure_ascii=False, default=str),
            model_path=model_path,
            is_active=is_active,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def active(self) -> MLModelRunModel | None:
        return await self.session.scalar(
            select(MLModelRunModel).where(MLModelRunModel.is_active.is_(True)).limit(1)
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
