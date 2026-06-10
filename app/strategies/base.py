from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.market.features import FeatureSnapshot


@dataclass(frozen=True)
class StrategySignal:
    exchange: str
    symbol: str
    direction: str
    strategy_key: str
    strategy_profile_key: str | None
    paper_profile_key: str | None
    score: int
    reasons: list[str]
    entry_reference: float
    invalidation_level: float | None
    suggested_stop_pct: float
    suggested_take_pct: float
    market_context: dict[str, Any]
    strategy_instance_id: str | None = None
    confidence: float = 0.0


class Strategy(Protocol):
    key: str
    name: str

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> StrategySignal | None: ...


def context_from_snapshot(snapshot: FeatureSnapshot) -> dict[str, Any]:
    return {
        "price": snapshot.price,
        "price_change_5m_pct": snapshot.price_change_5m_pct,
        "volume_1m_usd": snapshot.volume_1m_usd,
        "volume_5m_usd": snapshot.volume_5m_usd,
        "avg_volume_5m_usd": snapshot.avg_volume_5m_usd,
        "volume_spike_ratio": snapshot.volume_spike_ratio,
        "oi": snapshot.oi,
        "oi_change_5m_pct": snapshot.oi_change_5m_pct,
        "oi_change_15m_pct": snapshot.oi_change_15m_pct,
        "funding_rate_pct": snapshot.funding_rate_pct,
        "spread_pct": snapshot.spread_pct,
        "bid_depth_1pct": snapshot.bid_depth_1pct,
        "ask_depth_1pct": snapshot.ask_depth_1pct,
        "swept_low_30m": snapshot.swept_low_30m,
        "swept_high_30m": snapshot.swept_high_30m,
        "returned_after_low_sweep": snapshot.returned_after_low_sweep,
        "returned_after_high_sweep": snapshot.returned_after_high_sweep,
        "density_event": snapshot.density_event,
        "trend_context": snapshot.trend_context,
        "ml_signal_quality_score": snapshot.ml_signal_quality_score,
    }


def invalidation_level(direction: str, entry: float, stop_pct: float) -> float:
    distance = stop_pct / 100
    if direction == "LONG":
        return entry * (1 - distance)
    return entry * (1 + distance)
