from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.data.database import Database
from app.data.models import (
    BacktestRunModel,
    BacktestTradeModel,
    HistoricalCandleModel,
    MarketSnapshotModel,
    PaperTradeModel,
    SignalModel,
)
from app.utils.time import timeframe_minutes

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

    async def symbol_chart(self, symbol: str, days: int = 30, limit: int = 200) -> dict[str, Any]:
        """Every trade on one instrument, drawn on a single price series."""
        end = datetime.now(UTC).replace(tzinfo=None)
        start = end - timedelta(days=days)
        async with self.database.session() as session:
            trades = list(
                (
                    await session.scalars(
                        select(PaperTradeModel)
                        .where(
                            PaperTradeModel.symbol == symbol,
                            PaperTradeModel.opened_at >= start,
                        )
                        .order_by(PaperTradeModel.opened_at)
                        .limit(limit)
                    )
                ).all()
            )
            if trades:
                first = _naive(trades[0].opened_at) or start
                last = max(
                    (_naive(row.closed_at) or _naive(row.opened_at) or start) for row in trades
                )
                start = min(start, first - timedelta(hours=2))
                end = max(end, last + timedelta(hours=2))
            series, source, timeframe = await self._price_series(session, symbol, start, end)

        markers = []
        for trade in trades:
            markers.append(
                _marker(
                    "entry",
                    f"#{trade.id} вход {_ru_direction(trade.direction)}",
                    _naive(trade.opened_at),
                    trade.entry_price,
                    trade_id=trade.id,
                )
            )
            if trade.closed_at is not None and trade.exit_price:
                kind = "take" if trade.status == "CLOSED_TP" else "stop"
                markers.append(
                    _marker(
                        kind if trade.status in ("CLOSED_TP", "CLOSED_SL") else "exit",
                        f"#{trade.id} {_exit_label(trade.status).lower()}",
                        _naive(trade.closed_at),
                        trade.exit_price,
                        trade_id=trade.id,
                    )
                )
        return {
            "symbol": symbol,
            "series": series,
            "source": source,
            "timeframe": timeframe,
            "levels": [],
            "markers": [item for item in markers if item is not None],
            "trades": [_trade_payload(trade) for trade in trades],
        }

    async def backtest_chart(self, run_id: int, limit: int = 200) -> dict[str, Any] | None:
        """Backtest trades over the exact candles they were simulated on."""
        async with self.database.session() as session:
            run = await session.get(BacktestRunModel, run_id)
            if run is None:
                return None
            trades = list(
                (
                    await session.scalars(
                        select(BacktestTradeModel)
                        .where(BacktestTradeModel.run_id == run_id)
                        .order_by(BacktestTradeModel.entry_time)
                        .limit(limit)
                    )
                ).all()
            )
            candles = list(
                (
                    await session.scalars(
                        select(HistoricalCandleModel)
                        .where(
                            HistoricalCandleModel.symbol == run.symbol,
                            HistoricalCandleModel.timeframe == run.timeframe,
                            HistoricalCandleModel.open_time >= run.period_start,
                            HistoricalCandleModel.open_time <= run.period_end,
                        )
                        .order_by(HistoricalCandleModel.open_time)
                    )
                ).all()
            )

        series = [
            {
                "t": _iso(row.open_time),
                "o": row.open,
                "h": row.high,
                "l": row.low,
                "c": row.close,
            }
            for row in candles
        ]
        step = timeframe_minutes(run.timeframe)
        markers: list[dict[str, Any]] = []
        channels: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        for trade in trades:
            won = (trade.pnl_usd or 0) > 0
            markers.append(
                _marker(
                    "entry",
                    f"#{trade.id} вход {_ru_direction(trade.direction)}",
                    _naive(trade.entry_time),
                    trade.entry_price,
                    trade_id=trade.id,
                    won=won,
                )
            )
            markers.append(
                _marker(
                    "take" if won else "stop",
                    f"#{trade.id} выход {trade.pnl_pct:+.2f}%",
                    _naive(trade.exit_time),
                    trade.exit_price,
                    trade_id=trade.id,
                    won=won,
                )
            )
            context = _load_json(trade.context_json)
            channel = (context or {}).get("channel")
            if channel:
                channels.append(_channel_shape(trade, channel, step))
            rows.append(
                {
                    "id": trade.id,
                    "direction": trade.direction,
                    "status": trade.status,
                    "score": trade.score,
                    "entry_time": trade.entry_time,
                    "exit_time": trade.exit_time,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "stop_price": trade.stop_price,
                    "take_price": trade.take_price,
                    "pnl_usd": trade.pnl_usd,
                    "pnl_pct": trade.pnl_pct,
                    "mfe_pct": trade.mfe_pct,
                    "mae_pct": trade.mae_pct,
                    "channel": channel,
                }
            )
        return {
            "run": {
                "id": run.id,
                "strategy_key": run.strategy_key,
                "symbol": run.symbol,
                "timeframe": run.timeframe,
                "period_start": run.period_start,
                "period_end": run.period_end,
                "metrics": _load_json(run.metrics_json) or {},
            },
            "series": series,
            "source": "candles",
            "timeframe": run.timeframe,
            "levels": [],
            "markers": [item for item in markers if item is not None],
            "channels": channels,
            "trades": rows,
        }

    async def traded_symbols(self, days: int = 30) -> list[dict[str, Any]]:
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    select(
                        PaperTradeModel.symbol,
                        func.count(PaperTradeModel.id),
                        func.sum(PaperTradeModel.pnl_usd),
                    )
                    .where(PaperTradeModel.opened_at >= since)
                    .group_by(PaperTradeModel.symbol)
                    .order_by(func.count(PaperTradeModel.id).desc())
                )
            ).all()
        return [
            {"symbol": symbol, "trades": int(count or 0), "net_pnl": float(pnl or 0.0)}
            for symbol, count, pnl in rows
        ]

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
    kind: str,
    label: str,
    moment: datetime | None,
    value: float | None,
    trade_id: int | None = None,
    won: bool | None = None,
) -> dict[str, Any] | None:
    if not value:
        return None
    marker: dict[str, Any] = {
        "kind": kind,
        "label": label,
        "t": _iso(moment) if moment else None,
        "value": float(value),
    }
    if trade_id is not None:
        marker["trade_id"] = trade_id
    if won is not None:
        marker["won"] = won
    return marker


def _ru_direction(direction: str) -> str:
    return {"LONG": "лонг", "SHORT": "шорт"}.get(direction, direction)


def _load_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _channel_shape(
    trade: BacktestTradeModel, channel: dict[str, Any], step_minutes: int
) -> dict[str, Any]:
    """Rebuild the two boundary lines from the geometry stored at entry.

    The strategy records the boundaries at the entry bar plus a slope per candle, which is
    enough to extend both lines back to the pivots that defined them and forward to the exit.
    """
    entry_time = _naive(trade.entry_time) or datetime.now(UTC).replace(tzinfo=None)
    exit_time = _naive(trade.exit_time) or entry_time
    upper = float(channel.get("upper") or 0.0)
    lower = float(channel.get("lower") or 0.0)
    slope_abs = float(channel.get("slope_pct_per_candle") or 0.0) / 100 * (trade.entry_price or 0.0)

    bars_back = max([int(value) for value in (channel.get("point_bars_ago") or [0])] or [0])
    bars_forward = max(1, int((exit_time - entry_time).total_seconds() // 60 // max(1, step_minutes)))
    start_time = entry_time - timedelta(minutes=step_minutes * bars_back)
    end_time = entry_time + timedelta(minutes=step_minutes * bars_forward)

    return {
        "trade_id": trade.id,
        "direction": trade.direction,
        "touch_side": channel.get("touch_side"),
        "won": (trade.pnl_usd or 0) > 0,
        "start": _iso(start_time),
        "entry": _iso(entry_time),
        "end": _iso(end_time),
        "upper_start": upper - slope_abs * bars_back,
        "upper_end": upper + slope_abs * bars_forward,
        "lower_start": lower - slope_abs * bars_back,
        "lower_end": lower + slope_abs * bars_forward,
        "points": _channel_points(trade, channel, step_minutes, upper, lower, slope_abs),
        "entry_price": trade.entry_price,
        "stop_price": trade.stop_price,
        "take_price": trade.take_price,
        "exit_price": trade.exit_price,
        "exit": _iso(exit_time),
        "width_pct": channel.get("width_pct"),
        "touch_gap_pct": channel.get("touch_gap_pct"),
        "touch_pierced": channel.get("touch_pierced"),
        "rr": channel.get("rr"),
    }


def _channel_points(
    trade: BacktestTradeModel,
    channel: dict[str, Any],
    step_minutes: int,
    upper: float,
    lower: float,
    slope_abs: float,
) -> list[dict[str, Any]]:
    """The four touches that define the setup, numbered as the strategy sees them.

    Points 1 and 3 sit on the anchor line, point 2 on the parallel one, and point 4 is
    the entry itself - the touch of the boundary opposite point 3.
    """
    entry_time = _naive(trade.entry_time)
    bars_ago = [int(value) for value in (channel.get("point_bars_ago") or [])]
    if entry_time is None or len(bars_ago) < 3:
        return []
    anchor_at_entry = upper if channel.get("anchor_side") == "upper" else lower
    parallel_at_entry = lower if channel.get("anchor_side") == "upper" else upper

    points = []
    for number, (bars, base) in enumerate(
        zip(
            bars_ago[:3],
            [anchor_at_entry, parallel_at_entry, anchor_at_entry],
            strict=False,
        ),
        start=1,
    ):
        points.append(
            {
                "number": number,
                "t": _iso(entry_time - timedelta(minutes=step_minutes * bars)),
                "value": base - slope_abs * bars,
            }
        )
    points.append({"number": 4, "t": _iso(entry_time), "value": trade.entry_price})
    return points


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
