from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.exchanges.base import OrderbookEvent, TickerEvent, TradeEvent
from app.market.density_tracker import DensityEvent, DensityLevel, DensityTracker
from app.market.orderbook import OrderbookMetrics, calculate_orderbook_metrics
from app.market.trend import TrendAnalyzer
from app.utils.math import pct_change
from app.utils.time import utc_now


@dataclass(frozen=True)
class FeatureSnapshot:
    exchange: str
    symbol: str
    timestamp: datetime
    price: float
    price_change_5m_pct: float | None
    volume_1m_usd: float
    volume_5m_usd: float
    avg_volume_5m_usd: float
    volume_spike_ratio: float
    oi: float | None
    oi_change_5m_pct: float | None
    oi_change_15m_pct: float | None
    funding_rate_pct: float | None
    spread_pct: float | None
    bid_depth_1pct: float | None
    ask_depth_1pct: float | None
    swept_low_30m: float | None
    swept_high_30m: float | None
    returned_after_low_sweep: bool
    returned_after_high_sweep: bool
    density_event: dict | None = None
    trend_context: dict | None = None
    ml_signal_quality_score: float | None = None


class MarketFeatureStore:
    def __init__(
        self,
        retention_minutes: int = 30,
        max_price_points_per_symbol: int = 3000,
        max_trade_points_per_symbol: int = 3000,
        max_oi_points_per_symbol: int = 1000,
    ) -> None:
        self.retention = timedelta(minutes=retention_minutes)
        self.max_price_points_per_symbol = max_price_points_per_symbol
        self.max_trade_points_per_symbol = max_trade_points_per_symbol
        self.max_oi_points_per_symbol = max_oi_points_per_symbol
        self.prices: dict[tuple[str, str], deque[tuple[datetime, float]]] = defaultdict(
            lambda: deque(maxlen=max_price_points_per_symbol)
        )
        self.trades: dict[tuple[str, str], deque[tuple[datetime, str, float, float, float]]] = (
            defaultdict(lambda: deque(maxlen=max_trade_points_per_symbol))
        )
        self.oi: dict[tuple[str, str], deque[tuple[datetime, float]]] = defaultdict(
            lambda: deque(maxlen=max_oi_points_per_symbol)
        )
        self.funding: dict[tuple[str, str], float | None] = {}
        self.orderbooks: dict[tuple[str, str], OrderbookMetrics] = {}
        self.density_tracker = DensityTracker()
        self.trend_analyzer = TrendAnalyzer()

    def on_ticker(self, event: TickerEvent) -> None:
        key = (event.exchange, event.symbol)
        self.prices[key].append((event.timestamp, event.price))
        if event.open_interest is not None and event.open_interest > 0:
            self.oi[key].append((event.timestamp, event.open_interest))
        if event.funding_rate is not None:
            self.funding[key] = event.funding_rate * 100
        self._trim_key(key, event.timestamp)

    def on_trade(self, event: TradeEvent) -> None:
        key = (event.exchange, event.symbol)
        self.trades[key].append((event.timestamp, event.side, event.price, event.qty, event.usd))
        self.prices[key].append((event.timestamp, event.price))
        self._trim_key(key, event.timestamp)

    def on_orderbook(self, event: OrderbookEvent) -> None:
        metrics = calculate_orderbook_metrics(event.bids, event.asks)
        if metrics is not None:
            self.orderbooks[(event.exchange, event.symbol)] = metrics
            self.prices[(event.exchange, event.symbol)].append((event.timestamp, metrics.mid))
            self.density_tracker.update(event, metrics.mid)
            self._trim_key((event.exchange, event.symbol), event.timestamp)

    def on_open_interest(
        self, exchange: str, symbol: str, timestamp: datetime, open_interest: float
    ) -> None:
        key = (exchange, symbol)
        self.oi[key].append((timestamp, open_interest))
        self._trim_key(key, timestamp)

    def seed_candle(
        self,
        exchange: str,
        symbol: str,
        timestamp: datetime,
        close_price: float,
        turnover_usd: float,
    ) -> None:
        key = (exchange, symbol)
        self.prices[key].append((timestamp, close_price))
        if turnover_usd > 0:
            self.trades[key].append((timestamp, "seed", close_price, 0.0, turnover_usd))
        self._trim_key(key, utc_now())

    def snapshot(
        self,
        exchange: str,
        symbol: str,
        sweep_lookback_minutes: int = 30,
        sweep_return_minutes: int = 5,
    ) -> FeatureSnapshot | None:
        key = (exchange, symbol)
        price = self.latest_price(exchange, symbol)
        if price is None:
            return None
        now = utc_now()
        price_5m_ago = self._value_at_or_before(self.prices[key], now - timedelta(minutes=5))
        price_change_5m = pct_change(price_5m_ago, price)
        volume_1m = self._sum_volume(key, now - timedelta(minutes=1), now)
        volume_5m = self._sum_volume(key, now - timedelta(minutes=5), now)
        volume_60m = self._sum_volume(key, now - timedelta(minutes=60), now)
        avg_5m = volume_60m / 12 if volume_60m > 0 else volume_5m
        volume_spike = volume_5m / avg_5m if avg_5m > 0 else 0.0
        oi_now = self.latest_oi(exchange, symbol)
        oi_5m_ago = self._value_at_or_before(self.oi[key], now - timedelta(minutes=5))
        oi_15m_ago = self._value_at_or_before(self.oi[key], now - timedelta(minutes=15))
        book = self.orderbooks.get(key)
        density_event = self.density_tracker.latest_actionable_event(exchange, symbol)
        trend_context = self.trend_analyzer.analyze(
            list(self.prices.get(key, [])),
            list(self.trades.get(key, [])),
        )
        sweep = self._sweep_state(
            key=key,
            now=now,
            price=price,
            lookback=timedelta(minutes=sweep_lookback_minutes),
            return_window=timedelta(minutes=sweep_return_minutes),
        )
        return FeatureSnapshot(
            exchange=exchange,
            symbol=symbol,
            timestamp=now,
            price=price,
            price_change_5m_pct=price_change_5m,
            volume_1m_usd=volume_1m,
            volume_5m_usd=volume_5m,
            avg_volume_5m_usd=avg_5m,
            volume_spike_ratio=volume_spike,
            oi=oi_now,
            oi_change_5m_pct=pct_change(oi_5m_ago, oi_now),
            oi_change_15m_pct=pct_change(oi_15m_ago, oi_now),
            funding_rate_pct=self.funding.get(key),
            spread_pct=book.spread_pct if book else None,
            bid_depth_1pct=book.bid_depth_1pct if book else None,
            ask_depth_1pct=book.ask_depth_1pct if book else None,
            swept_low_30m=sweep["low"],
            swept_high_30m=sweep["high"],
            returned_after_low_sweep=bool(sweep["returned_low"]),
            returned_after_high_sweep=bool(sweep["returned_high"]),
            density_event=density_event.to_context() if density_event else None,
            trend_context=trend_context.to_context(),
            ml_signal_quality_score=None,
        )

    def drain_density_events(self, limit: int = 500) -> list[DensityEvent]:
        return self.density_tracker.drain_events(limit=limit)

    def active_density_levels(self) -> list[DensityLevel]:
        return self.density_tracker.active_levels()

    def latest_price(self, exchange: str, symbol: str) -> float | None:
        values = self.prices.get((exchange, symbol))
        if not values:
            return None
        return values[-1][1]

    def latest_oi(self, exchange: str, symbol: str) -> float | None:
        values = self.oi.get((exchange, symbol))
        if not values:
            return None
        return values[-1][1]

    def min_max_price_since(
        self,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> tuple[float | None, float | None, float | None]:
        values = [
            price for ts, price in self.prices.get((exchange, symbol), []) if start <= ts <= end
        ]
        if not values:
            return None, None, None
        return values[-1], min(values), max(values)

    def _sum_volume(self, key: tuple[str, str], start: datetime, end: datetime) -> float:
        return sum(usd for ts, _, _, _, usd in self.trades.get(key, []) if start <= ts <= end)

    def _value_at_or_before(
        self,
        values: deque[tuple[datetime, float]],
        target: datetime,
    ) -> float | None:
        candidate: float | None = None
        for ts, value in values:
            if ts <= target:
                candidate = value
            else:
                break
        return candidate

    def _sweep_state(
        self,
        key: tuple[str, str],
        now: datetime,
        price: float,
        lookback: timedelta,
        return_window: timedelta,
    ) -> dict[str, float | bool | None]:
        values = list(self.prices.get(key, []))
        past_start = now - lookback
        recent_start = now - return_window
        older = [p for ts, p in values if past_start <= ts < recent_start]
        recent = [p for ts, p in values if recent_start <= ts <= now]
        if not older or not recent:
            return {"low": None, "high": None, "returned_low": False, "returned_high": False}
        local_low = min(older)
        local_high = max(older)
        broke_low = min(recent) < local_low
        broke_high = max(recent) > local_high
        return {
            "low": local_low if broke_low else None,
            "high": local_high if broke_high else None,
            "returned_low": bool(broke_low and price > local_low),
            "returned_high": bool(broke_high and price < local_high),
        }

    def _trim_key(self, key: tuple[str, str], now: datetime) -> None:
        cutoff = now - self.retention
        for store in (self.prices, self.trades, self.oi):
            values = store.get(key)
            if not values:
                continue
            while values and values[0][0] < cutoff:
                values.popleft()

    def trim_all(self, now: datetime | None = None) -> None:
        now = now or utc_now()
        for key in set(self.prices) | set(self.trades) | set(self.oi):
            self._trim_key(key, now)

    def memory_counts(self) -> dict[str, int]:
        return {
            "price_points": sum(len(values) for values in self.prices.values()),
            "trade_points": sum(len(values) for values in self.trades.values()),
            "oi_points": sum(len(values) for values in self.oi.values()),
            "orderbooks": len(self.orderbooks),
        }
