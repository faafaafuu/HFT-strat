from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.data.database import Database
from app.data.models import (
    HistoricalCandleModel,
    MarketSnapshotModel,
    PaperTradeModel,
    SignalModel,
)

# Candles are only downloaded for a few majors; snapshots exist for every traded symbol.
_CANDLE_TIMEFRAMES = ("1m", "15m", "1h", "4h")
_MAX_POINTS = 600


class ChartService:
    """Price series plus trade/signal levels for the interactive charts."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def trade_chart(self, trade_id: int) -> dict[str, Any] | None:
        async with self.database.session() as session:
            trade = await session.get(PaperTradeModel, trade_id)
            if trade is None:
                return None
            now = datetime.now(UTC).replace(tzinfo=None)
            opened = _naive(trade.opened_at) or now
            closed = _naive(trade.closed_at) or now
            span = max(closed - opened, timedelta(minutes=30))
            pad = min(max(span * 0.35, timedelta(minutes=15)), timedelta(hours=12))
            series, source, timeframe = await self._price_series(
                session, trade.symbol, opened - pad, closed + pad
            )

        levels = [
            _level("entry", "Вход", trade.entry_price),
            _level("stop", "Стоп-лосс", trade.stop_price),
            _level("take", "Тейк-профит", trade.take_price),
        ]
        if trade.exit_price:
            levels.append(_level("exit", "Выход", trade.exit_price))
        markers = [
            _marker("entry", "Вход", opened, trade.entry_price),
        ]
        if trade.closed_at is not None and trade.exit_price:
            markers.append(
                _marker("exit", _exit_label(trade.status), _naive(trade.closed_at), trade.exit_price)
            )
        if trade.partial_closed and trade.partial_exit_price:
            markers.append(
                _marker("partial", "Частичная фиксация", None, trade.partial_exit_price)
            )

        return {
            "trade": _trade_payload(trade),
            "series": series,
            "source": source,
            "timeframe": timeframe,
            "levels": [item for item in levels if item is not None],
            "markers": [item for item in markers if item is not None],
        }

    async def signal_chart(self, signal_id: int) -> dict[str, Any] | None:
        async with self.database.session() as session:
            signal = await session.get(SignalModel, signal_id)
            if signal is None:
                return None
            moment = _naive(signal.timestamp) or datetime.now(UTC).replace(tzinfo=None)
            series, source, timeframe = await self._price_series(
                session, signal.symbol, moment - timedelta(hours=2), moment + timedelta(hours=4)
            )

        entry = signal.entry_price or 0.0
        levels = [_level("entry", "Вход", entry)]
        if signal.suggested_stop_pct:
            direction = -1 if signal.direction == "LONG" else 1
            levels.append(
                _level("stop", "Предлагаемый стоп", entry * (1 + direction * signal.suggested_stop_pct / 100))
            )
        if signal.suggested_take_pct:
            direction = 1 if signal.direction == "LONG" else -1
            levels.append(
                _level("take", "Предлагаемый тейк", entry * (1 + direction * signal.suggested_take_pct / 100))
            )
        if signal.invalidation_level:
            levels.append(_level("invalidation", "Инвалидация", signal.invalidation_level))

        return {
            "series": series,
            "source": source,
            "timeframe": timeframe,
            "levels": [item for item in levels if item is not None],
            "markers": [item for item in [_marker("entry", "Сигнал", moment, entry)] if item],
        }

    async def _price_series(
        self,
        session: Any,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[dict[str, Any]], str, str | None]:
        timeframe = _pick_timeframe(end - start)
        for candidate in _timeframe_order(timeframe):
            rows = list(
                (
                    await session.scalars(
                        select(HistoricalCandleModel)
                        .where(
                            HistoricalCandleModel.symbol == symbol,
                            HistoricalCandleModel.timeframe == candidate,
                            HistoricalCandleModel.open_time >= start,
                            HistoricalCandleModel.open_time <= end,
                        )
                        .order_by(HistoricalCandleModel.open_time)
                        .limit(_MAX_POINTS)
                    )
                ).all()
            )
            if len(rows) >= 5:
                return (
                    [
                        {
                            "t": _iso(row.open_time),
                            "o": row.open,
                            "h": row.high,
                            "l": row.low,
                            "c": row.close,
                        }
                        for row in rows
                    ],
                    "candles",
                    candidate,
                )

        snapshots = list(
            (
                await session.scalars(
                    select(MarketSnapshotModel)
                    .where(
                        MarketSnapshotModel.symbol == symbol,
                        MarketSnapshotModel.timestamp >= start,
                        MarketSnapshotModel.timestamp <= end,
                    )
                    .order_by(MarketSnapshotModel.timestamp)
                    .limit(_MAX_POINTS)
                )
            ).all()
        )
        return (
            [{"t": _iso(row.timestamp), "c": row.price} for row in snapshots if row.price],
            "snapshots",
            None,
        )


def _trade_payload(trade: PaperTradeModel) -> dict[str, Any]:
    return {
        "id": trade.id,
        "symbol": trade.symbol,
        "direction": trade.direction,
        "status": trade.status,
        "pattern": trade.pattern,
        "score": trade.score,
        "strategy_key": trade.strategy_key,
        "strategy_instance_id": trade.strategy_instance_id,
        "profile_key": trade.profile_key,
        "entry_price": trade.entry_price,
        "stop_price": trade.stop_price,
        "take_price": trade.take_price,
        "exit_price": trade.exit_price,
        "leverage": trade.leverage,
        "position_size_usd": trade.position_size_usd,
        "risk_usd": trade.risk_usd,
        "pnl_usd": trade.pnl_usd,
        "pnl_pct": trade.pnl_pct,
        "fees_usd": trade.fees_usd,
        "realized_rr": trade.realized_rr,
        "opened_at": trade.opened_at,
        "closed_at": trade.closed_at,
        "signal_id": trade.signal_id,
    }


def _pick_timeframe(span: timedelta) -> str:
    hours = span.total_seconds() / 3600
    if hours <= 12:
        return "1m"
    if hours <= 96:
        return "15m"
    if hours <= 720:
        return "1h"
    return "4h"


def _timeframe_order(preferred: str) -> list[str]:
    index = _CANDLE_TIMEFRAMES.index(preferred)
    return list(_CANDLE_TIMEFRAMES[index:])


def _level(kind: str, label: str, value: float | None) -> dict[str, Any] | None:
    if not value:
        return None
    return {"kind": kind, "label": label, "value": float(value)}


def _marker(
    kind: str, label: str, moment: datetime | None, value: float | None
) -> dict[str, Any] | None:
    if not value:
        return None
    return {
        "kind": kind,
        "label": label,
        "t": _iso(moment) if moment else None,
        "value": float(value),
    }


def _exit_label(status: str) -> str:
    return {
        "CLOSED_TP": "Выход по тейку",
        "CLOSED_SL": "Выход по стопу",
        "EXPIRED": "Выход по таймауту",
    }.get(status, "Выход")


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()
