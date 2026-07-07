from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.backtesting.metrics import compute_backtest_metrics
from app.backtesting.simulator import SimulatedTrade, simulate_exit
from app.config import Settings
from app.data.database import Database
from app.data.models import DensityEventModel, HistoricalCandleModel, MarketSnapshotModel
from app.data.repositories import BacktestRepository, HistoricalDataRepository
from app.market.features import FeatureSnapshot
from app.strategies.registry import default_registry

# Strategies that cannot produce a single signal without open interest history.
OI_REQUIRED_STRATEGIES = {"oi_pump_price_move", "oi_momentum_scalper"}

MIN_BACKTEST_CANDLES = 120
SNAPSHOT_MAX_AGE_SEC = 180.0
SNAPSHOT_WINDOW_CANDLES = 60


@dataclass(frozen=True)
class MarketSnapshotPoint:
    timestamp: datetime
    oi: float | None
    oi_change_5m_pct: float | None
    oi_change_15m_pct: float | None
    funding_rate_pct: float | None
    spread_pct: float | None
    bid_depth_1pct: float | None
    ask_depth_1pct: float | None


class MarketSnapshotSeries:
    """Point-in-time lookup of persisted market snapshots (OI, spread, depth)."""

    def __init__(
        self,
        points: list[MarketSnapshotPoint],
        max_age_sec: float = SNAPSHOT_MAX_AGE_SEC,
    ) -> None:
        self._points = sorted(points, key=lambda point: point.timestamp)
        self._timestamps = [point.timestamp for point in self._points]
        self.max_age = timedelta(seconds=max_age_sec)

    def __len__(self) -> int:
        return len(self._points)

    def at(self, timestamp: datetime) -> MarketSnapshotPoint | None:
        index = bisect_right(self._timestamps, timestamp) - 1
        if index < 0:
            return None
        point = self._points[index]
        if timestamp - point.timestamp > self.max_age:
            return None
        return point


class DensityEventSeries:
    """Point-in-time lookup of persisted density events for the density backtest."""

    def __init__(self, events: list[Any]) -> None:
        rows = sorted(events, key=lambda event: event.timestamp)
        self._timestamps = [event.timestamp for event in rows]
        self._events = rows

    def __len__(self) -> int:
        return len(self._events)

    def at(self, timestamp: datetime, max_age: timedelta) -> dict[str, Any] | None:
        index = bisect_right(self._timestamps, timestamp) - 1
        if index < 0:
            return None
        event = self._events[index]
        if timestamp - event.timestamp > max_age:
            return None
        return {
            "side": event.side,
            "price": event.price,
            "size_usd": event.size_usd,
            "distance_pct": event.distance_pct,
            "lifetime_sec": event.lifetime_sec,
            "event_type": event.event_type,
            "pulled_pct": event.pulled_pct,
            "eaten_pct": event.eaten_pct,
            "refill_count": event.refill_count,
            "absorption_score": event.absorption_score,
            "spoof_score": event.spoof_score,
        }


class BacktestEngine:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.registry = default_registry(settings)

    async def run(
        self,
        *,
        strategy_key: str,
        symbol: str,
        timeframe: str = "1m",
        days: int | None = None,
        params: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        params = params or {}
        days = days or self.settings.backtest.default_days
        async with self.database.session() as session:
            repo = HistoricalDataRepository(session)
            latest = await session.scalar(
                select(func.max(HistoricalCandleModel.open_time)).where(
                    HistoricalCandleModel.exchange == "bybit",
                    HistoricalCandleModel.symbol == symbol,
                    HistoricalCandleModel.timeframe == timeframe,
                )
            )
            if latest is None:
                coverage = await repo.coverage()
                return _empty_result(
                    strategy_key,
                    timeframe,
                    status="no_candles",
                    message=(
                        f"Нет исторических свечей для {symbol} {timeframe}. "
                        "Скачайте историю (job download_history или tools/download_history.py)."
                    ),
                    extra={"coverage": coverage},
                )
            # Anchor the window to the last available candle, not to "now":
            # stale history must still be backtestable.
            since = latest - timedelta(days=days)
            candles = await repo.candles("bybit", symbol, timeframe, since=since)
            density_events = []
            if strategy_key == "density_strategy":
                density_events = list(
                    (
                        await session.scalars(
                            select(DensityEventModel)
                            .where(
                                DensityEventModel.symbol == symbol,
                                DensityEventModel.timestamp >= since,
                            )
                            .order_by(DensityEventModel.timestamp.asc())
                        )
                    ).all()
                )
            snapshots = await _load_snapshot_series(session, symbol, since)
        if strategy_key == "density_strategy" and not density_events:
            return _empty_result(
                strategy_key,
                timeframe,
                status="insufficient_density_history",
                message=(
                    "Недостаточно исторических L2/orderbook данных для корректного "
                    "density backtest."
                ),
            )
        if len(candles) < MIN_BACKTEST_CANDLES:
            return _empty_result(
                strategy_key,
                timeframe,
                status="insufficient_candles",
                message=(
                    f"Найдено только {len(candles)} свечей {symbol} {timeframe} "
                    f"за последние {days} дн. данных (до {latest}). "
                    f"Нужно минимум {MIN_BACKTEST_CANDLES}."
                ),
            )
        density_series = DensityEventSeries(density_events) if density_events else None
        if strategy_key in OI_REQUIRED_STRATEGIES and not len(snapshots):
            return _empty_result(
                strategy_key,
                timeframe,
                status="no_oi_history",
                message=(
                    f"Стратегия {strategy_key} требует историю открытого интереса, "
                    f"но в market_snapshots нет данных по {symbol} за период. "
                    "OI пишется во время работы бота (persist_market_snapshots)."
                ),
            )
        result = self.run_on_candles(
            strategy_key=strategy_key,
            candles=candles,
            timeframe=timeframe,
            params=params,
            snapshots=snapshots,
            density_events=density_series,
        )
        result["status"] = "ok"
        result["period_start"] = candles[0].open_time
        result["period_end"] = candles[-1].open_time
        result["candle_count"] = len(candles)
        result["snapshot_points"] = len(snapshots)
        if persist:
            async with self.database.session() as session:
                run = await BacktestRepository(session).create_run(
                    strategy_key=strategy_key,
                    symbol=symbol,
                    timeframe=timeframe,
                    period_start=candles[0].open_time,
                    period_end=candles[-1].open_time,
                    params=params,
                    metrics=result["metrics"],
                    trades=result["trade_rows"],
                    equity_curve=result["equity_curve"],
                )
                result["run_id"] = run.id
        return result

    def run_on_candles(
        self,
        *,
        strategy_key: str,
        candles: list[HistoricalCandleModel],
        timeframe: str,
        params: dict[str, Any] | None = None,
        snapshots: MarketSnapshotSeries | None = None,
        density_events: DensityEventSeries | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        strategy = self.registry.get(strategy_key)
        if strategy is None:
            raise ValueError(f"Unknown strategy: {strategy_key}")
        balance = float(params.get("initial_balance", self.settings.paper.initial_balance))
        initial_balance = balance
        position_size = float(params.get("position_size_usd", balance))
        stop_pct = float(params.get("stop_loss_pct", self.settings.paper.stop_pct))
        take_pct = float(params.get("take_profit_pct", self.settings.paper.take_pct))
        max_holding = int(params.get("max_holding_candles", _holding_candles(timeframe, 180)))
        trade_gap_until: datetime | None = None
        trades: list[SimulatedTrade] = []
        equity_curve: list[dict[str, Any]] = []
        peak = initial_balance
        step = timedelta(minutes=int(timeframe.rstrip("m")) if timeframe.endswith("m") else 1)
        warmup = min(max(60, _holding_candles(timeframe, 60)), max(1, len(candles) // 3))
        for index in range(warmup, len(candles) - 1):
            candle = candles[index]
            if trade_gap_until is not None and candle.open_time <= trade_gap_until:
                continue
            window = candles[max(0, index + 1 - SNAPSHOT_WINDOW_CANDLES) : index + 1]
            point = snapshots.at(candle.open_time) if snapshots is not None else None
            density_event = (
                density_events.at(candle.open_time + step, max_age=step)
                if density_events is not None
                else None
            )
            snapshot = _snapshot_from_candles(window, point, density_event)
            signal = strategy.generate_signal(snapshot)
            if signal is None:
                continue
            if signal.score < int(params.get("min_score", self.settings.signals.min_score)):
                continue
            future = candles[index + 1 : index + 1 + max_holding]
            trade = simulate_exit(
                direction=signal.direction,
                entry_time=candle.open_time,
                entry_price=candle.close,
                future_candles=future,
                stop_pct=float(params.get("stop_loss_pct", signal.suggested_stop_pct or stop_pct)),
                take_pct=float(params.get("take_profit_pct", signal.suggested_take_pct or take_pct)),
                max_holding_candles=max_holding,
                position_size_usd=position_size,
                taker_fee_pct=self.settings.paper.taker_fee_pct,
                slippage_pct=self.settings.paper.slippage_pct,
            )
            if trade is None:
                continue
            trades.append(trade)
            balance += trade.pnl_usd
            trade_gap_until = trade.exit_time
            peak = max(peak, balance)
            drawdown = (peak - balance) / peak * 100 if peak else 0.0
            equity_curve.append(
                {
                    "timestamp": trade.exit_time,
                    "equity": balance,
                    "balance": balance,
                    "drawdown_pct": drawdown,
                }
            )
        metrics = compute_backtest_metrics(
            trades, initial_balance=initial_balance, final_balance=balance, equity_curve=equity_curve
        )
        return {
            "strategy_key": strategy_key,
            "timeframe": timeframe,
            "metrics": metrics,
            "trades": trades,
            "trade_rows": [_trade_row(strategy_key, trade, candles[0].exchange, candles[0].symbol) for trade in trades] if candles else [],
            "equity_curve": equity_curve,
        }


async def _load_snapshot_series(session, symbol: str, since: datetime) -> MarketSnapshotSeries:
    rows = (
        await session.execute(
            select(
                MarketSnapshotModel.timestamp,
                MarketSnapshotModel.oi,
                MarketSnapshotModel.oi_change_5m,
                MarketSnapshotModel.oi_change_15m,
                MarketSnapshotModel.funding_rate,
                MarketSnapshotModel.spread_pct,
                MarketSnapshotModel.bid_depth_1pct,
                MarketSnapshotModel.ask_depth_1pct,
            )
            .where(
                MarketSnapshotModel.symbol == symbol,
                MarketSnapshotModel.timestamp >= since,
            )
            .order_by(MarketSnapshotModel.timestamp.asc())
        )
    ).all()
    points = [
        MarketSnapshotPoint(
            timestamp=timestamp,
            oi=oi,
            oi_change_5m_pct=oi_change_5m,
            oi_change_15m_pct=oi_change_15m,
            funding_rate_pct=funding_rate,
            spread_pct=spread_pct,
            bid_depth_1pct=bid_depth,
            ask_depth_1pct=ask_depth,
        )
        for timestamp, oi, oi_change_5m, oi_change_15m, funding_rate, spread_pct, bid_depth, ask_depth in rows
    ]
    return MarketSnapshotSeries(points)


def _empty_result(
    strategy_key: str,
    timeframe: str,
    *,
    status: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "strategy_key": strategy_key,
        "timeframe": timeframe,
        "metrics": {},
        "trades": [],
        "trade_rows": [],
        "equity_curve": [],
        "status": status,
        "message": message,
    }
    if extra:
        result.update(extra)
    return result


def _snapshot_from_candles(
    candles: list[HistoricalCandleModel],
    point: MarketSnapshotPoint | None = None,
    density_event: dict[str, Any] | None = None,
) -> FeatureSnapshot:
    current = candles[-1]
    price_5m_ago = candles[-6].close if len(candles) >= 6 else candles[0].close
    volume_5m = sum(c.turnover or c.volume * c.close for c in candles[-5:])
    volume_60m = sum(c.turnover or c.volume * c.close for c in candles[-60:])
    avg_volume_5m = volume_60m / 12 if volume_60m > 0 else volume_5m
    older = candles[-35:-5] if len(candles) >= 35 else candles[:-5]
    recent = candles[-5:]
    local_low = min((c.low for c in older), default=None)
    local_high = max((c.high for c in older), default=None)
    recent_low = min(c.low for c in recent)
    recent_high = max(c.high for c in recent)
    returned_low = bool(local_low is not None and recent_low < local_low and current.close > local_low)
    returned_high = bool(local_high is not None and recent_high > local_high and current.close < local_high)
    return FeatureSnapshot(
        exchange=current.exchange,
        symbol=current.symbol,
        timestamp=current.open_time,
        price=current.close,
        price_change_5m_pct=_pct(price_5m_ago, current.close),
        volume_1m_usd=current.turnover or current.volume * current.close,
        volume_5m_usd=volume_5m,
        avg_volume_5m_usd=avg_volume_5m,
        volume_spike_ratio=volume_5m / avg_volume_5m if avg_volume_5m else 0.0,
        oi=point.oi if point else None,
        oi_change_5m_pct=point.oi_change_5m_pct if point else None,
        oi_change_15m_pct=point.oi_change_15m_pct if point else None,
        funding_rate_pct=point.funding_rate_pct if point else None,
        spread_pct=point.spread_pct if point else None,
        bid_depth_1pct=point.bid_depth_1pct if point else None,
        ask_depth_1pct=point.ask_depth_1pct if point else None,
        swept_low_30m=local_low if returned_low else None,
        swept_high_30m=local_high if returned_high else None,
        returned_after_low_sweep=returned_low,
        returned_after_high_sweep=returned_high,
        density_event=density_event,
    )


def _trade_row(strategy_key: str, trade: SimulatedTrade, exchange: str, symbol: str) -> dict[str, Any]:
    return {
        "exchange": exchange,
        "symbol": symbol,
        "strategy_key": strategy_key,
        "direction": trade.direction,
        "entry_time": trade.entry_time,
        "exit_time": trade.exit_time,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "stop_price": trade.stop_price,
        "take_price": trade.take_price,
        "pnl_usd": trade.pnl_usd,
        "fees_usd": trade.fees_usd,
        "pnl_pct": trade.pnl_pct,
        "mfe_pct": trade.mfe_pct,
        "mae_pct": trade.mae_pct,
        "status": trade.status,
    }


def _holding_candles(timeframe: str, minutes: int) -> int:
    step = int(timeframe.rstrip("m")) if timeframe.endswith("m") else 1
    return max(1, minutes // max(1, step))


def _pct(old: float, new: float) -> float:
    return (new - old) / old * 100 if old else 0.0
