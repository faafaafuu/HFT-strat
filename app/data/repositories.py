from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import (
    MarketSnapshotModel,
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
    ) -> SignalModel:
        signal = SignalModel(
            exchange=exchange,
            symbol=symbol,
            timestamp=timestamp,
            direction=direction,
            pattern=pattern,
            score=score,
            entry_price=entry_price,
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
    ) -> SignalModel | None:
        stmt = (
            select(SignalModel)
            .where(
                and_(
                    SignalModel.exchange == exchange,
                    SignalModel.symbol == symbol,
                    SignalModel.timestamp >= since,
                )
            )
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


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
