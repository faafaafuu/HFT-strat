from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from app.exchanges.base import OrderbookEvent


@dataclass
class DensityLevel:
    exchange: str
    symbol: str
    side: str
    price: float
    first_seen_at: datetime
    last_seen_at: datetime
    max_size_usd: float
    current_size_usd: float
    status: str = "holding"
    refill_count: int = 0
    touch_count: int = 0
    stats: dict[str, float | int | str] = field(default_factory=dict)

    @property
    def lifetime_sec(self) -> float:
        return max(0.0, (self.last_seen_at - self.first_seen_at).total_seconds())


@dataclass(frozen=True)
class DensityEvent:
    exchange: str
    symbol: str
    timestamp: datetime
    side: str
    price: float
    size_usd: float
    distance_pct: float
    lifetime_sec: float
    event_type: str
    pulled_pct: float = 0.0
    eaten_pct: float = 0.0
    refill_count: int = 0
    absorption_score: float = 0.0
    spoof_score: float = 0.0
    context: dict | None = None

    def to_context(self) -> dict:
        return {
            "side": self.side,
            "price": self.price,
            "size_usd": self.size_usd,
            "distance_pct": self.distance_pct,
            "lifetime_sec": self.lifetime_sec,
            "event_type": self.event_type,
            "pulled_pct": self.pulled_pct,
            "eaten_pct": self.eaten_pct,
            "refill_count": self.refill_count,
            "absorption_score": self.absorption_score,
            "spoof_score": self.spoof_score,
            "context": self.context or {},
        }


class DensityTracker:
    def __init__(
        self,
        *,
        min_density_usd: float = 500_000,
        max_distance_pct: float = 1.0,
        max_events: int = 5000,
    ) -> None:
        self.min_density_usd = min_density_usd
        self.max_distance_pct = max_distance_pct
        self.levels: dict[tuple[str, str, str, float], DensityLevel] = {}
        self.events: deque[DensityEvent] = deque(maxlen=max_events)
        self._emitted_holding: set[tuple[str, str, str, float]] = set()
        self._last_actionable: dict[tuple[str, str], DensityEvent] = {}

    def update(self, event: OrderbookEvent, mid_price: float) -> None:
        seen: set[tuple[str, str, str, float]] = set()
        for side, rows in (("bid", event.bids), ("ask", event.asks)):
            for price, qty in rows[:50]:
                size_usd = price * qty
                if size_usd < self.min_density_usd:
                    continue
                distance_pct = abs(price - mid_price) / mid_price * 100 if mid_price else 0.0
                if distance_pct > self.max_distance_pct:
                    continue
                key = (event.exchange, event.symbol, side, round(price, 8))
                seen.add(key)
                level = self.levels.get(key)
                if level is None:
                    level = DensityLevel(
                        exchange=event.exchange,
                        symbol=event.symbol,
                        side=side,
                        price=price,
                        first_seen_at=event.timestamp,
                        last_seen_at=event.timestamp,
                        max_size_usd=size_usd,
                        current_size_usd=size_usd,
                        status="appeared",
                    )
                    self.levels[key] = level
                    self._emit(level, event.timestamp, "appeared", distance_pct)
                    continue
                previous_size = level.current_size_usd
                level.last_seen_at = event.timestamp
                level.current_size_usd = size_usd
                if size_usd > level.max_size_usd * 1.1:
                    level.refill_count += 1
                    level.max_size_usd = size_usd
                    level.status = "refilled"
                    self._emit(level, event.timestamp, "refilled", distance_pct)
                elif level.lifetime_sec >= 10 and key not in self._emitted_holding:
                    level.status = "holding"
                    self._emitted_holding.add(key)
                    self._emit(level, event.timestamp, "holding", distance_pct)
                elif previous_size and size_usd < previous_size * 0.35:
                    level.status = "eaten"
                    eaten_pct = (previous_size - size_usd) / previous_size * 100
                    self._emit(level, event.timestamp, "eaten", distance_pct, eaten_pct=eaten_pct)

        missing = [key for key in self.levels if key[:2] == (event.exchange, event.symbol) and key not in seen]
        for key in missing[:200]:
            level = self.levels.pop(key)
            pulled_pct = 100.0
            event_type = "pulled" if level.lifetime_sec < 120 else "absorbed"
            spoof_score = 7.0 if event_type == "pulled" else 2.0
            absorption_score = 8.0 if event_type == "absorbed" else 0.0
            distance_pct = abs(level.price - mid_price) / mid_price * 100 if mid_price else 0.0
            self._emit(
                level,
                event.timestamp,
                event_type,
                distance_pct,
                pulled_pct=pulled_pct,
                absorption_score=absorption_score,
                spoof_score=spoof_score,
            )
            self._emitted_holding.discard(key)

    def latest_actionable_event(self, exchange: str, symbol: str) -> DensityEvent | None:
        return self._last_actionable.get((exchange, symbol))

    def drain_events(self, limit: int = 500) -> list[DensityEvent]:
        rows = []
        while self.events and len(rows) < limit:
            rows.append(self.events.popleft())
        return rows

    def active_levels(self) -> list[DensityLevel]:
        return list(self.levels.values())

    def _emit(
        self,
        level: DensityLevel,
        timestamp: datetime,
        event_type: str,
        distance_pct: float,
        *,
        pulled_pct: float = 0.0,
        eaten_pct: float = 0.0,
        absorption_score: float = 0.0,
        spoof_score: float = 0.0,
    ) -> None:
        event = DensityEvent(
            exchange=level.exchange,
            symbol=level.symbol,
            timestamp=timestamp,
            side=level.side,
            price=level.price,
            size_usd=level.current_size_usd,
            distance_pct=distance_pct,
            lifetime_sec=level.lifetime_sec,
            event_type=event_type,
            pulled_pct=pulled_pct,
            eaten_pct=eaten_pct,
            refill_count=level.refill_count,
            absorption_score=absorption_score,
            spoof_score=spoof_score,
            context={"max_size_usd": level.max_size_usd, "touch_count": level.touch_count},
        )
        self.events.append(event)
        if event_type in {"holding", "pulled", "eaten", "absorbed", "refilled"}:
            self._last_actionable[(level.exchange, level.symbol)] = event
