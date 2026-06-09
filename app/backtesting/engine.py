from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.backtesting.metrics import compute_backtest_metrics
from app.backtesting.simulator import SimulatedTrade, simulate_exit
from app.config import Settings
from app.data.database import Database
from app.data.models import HistoricalCandleModel
from app.data.repositories import BacktestRepository, HistoricalDataRepository
from app.market.features import FeatureSnapshot
from app.strategies.registry import default_registry
from app.utils.time import utc_now


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
        since = utc_now() - timedelta(days=days or self.settings.backtest.default_days)
        async with self.database.session() as session:
            candles = await HistoricalDataRepository(session).candles(
                "bybit", symbol, timeframe, since=since
            )
        result = self.run_on_candles(
            strategy_key=strategy_key,
            candles=candles,
            timeframe=timeframe,
            params=params,
        )
        if persist and candles:
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
        warmup = min(max(60, _holding_candles(timeframe, 60)), max(1, len(candles) // 3))
        for index in range(warmup, len(candles) - 1):
            candle = candles[index]
            if trade_gap_until is not None and candle.open_time <= trade_gap_until:
                continue
            snapshot = _snapshot_from_candles(candles[: index + 1])
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
            peak = max([initial_balance, *(row["equity"] for row in equity_curve), balance])
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


def _snapshot_from_candles(candles: list[HistoricalCandleModel]) -> FeatureSnapshot:
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
        oi=None,
        oi_change_5m_pct=None,
        oi_change_15m_pct=None,
        funding_rate_pct=None,
        spread_pct=0.01,
        bid_depth_1pct=None,
        ask_depth_1pct=None,
        swept_low_30m=local_low if returned_low else None,
        swept_high_30m=local_high if returned_high else None,
        returned_after_low_sweep=returned_low,
        returned_after_high_sweep=returned_high,
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
