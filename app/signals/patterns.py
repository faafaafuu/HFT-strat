from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.config import ThresholdsConfig
from app.market.features import FeatureSnapshot

Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class SignalCandidate:
    exchange: str
    symbol: str
    direction: Direction
    pattern: str
    entry_price: float
    reasons: list[str]
    context: dict[str, Any]


def detect_patterns(snapshot: FeatureSnapshot, thresholds: ThresholdsConfig) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    candidates.extend(_detect_oi_pump_price_move(snapshot, thresholds))
    candidates.extend(_detect_stop_hunt_sweep(snapshot, thresholds))
    return candidates


def _base_context(snapshot: FeatureSnapshot) -> dict[str, Any]:
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
    }


def _detect_oi_pump_price_move(
    snapshot: FeatureSnapshot,
    thresholds: ThresholdsConfig,
) -> list[SignalCandidate]:
    price_change = snapshot.price_change_5m_pct
    oi_change = snapshot.oi_change_15m_pct
    if price_change is None or oi_change is None:
        return []
    if abs(price_change) < thresholds.price_change_5m_pct:
        return []
    if oi_change < thresholds.oi_change_15m_pct:
        return []
    if snapshot.volume_spike_ratio < thresholds.volume_spike_multiplier:
        return []
    direction: Direction = "LONG" if price_change > 0 else "SHORT"
    reasons = [
        f"Price {'up' if direction == 'LONG' else 'down'} {price_change:.2f}% за 5m",
        f"OI +{oi_change:.2f}% за 15m",
        f"Volume {snapshot.volume_spike_ratio:.2f}x выше среднего",
    ]
    if snapshot.spread_pct is not None:
        reasons.append(f"Spread {snapshot.spread_pct:.3f}%")
    return [
        SignalCandidate(
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            direction=direction,
            pattern="oi_pump_price_move",
            entry_price=snapshot.price,
            reasons=reasons,
            context=_base_context(snapshot),
        )
    ]


def _detect_stop_hunt_sweep(
    snapshot: FeatureSnapshot,
    thresholds: ThresholdsConfig,
) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    oi_change = snapshot.oi_change_5m_pct or snapshot.oi_change_15m_pct or 0.0
    has_oi_growth = oi_change >= thresholds.oi_change_15m_pct / 2
    has_volume = snapshot.volume_spike_ratio >= max(1.0, thresholds.volume_spike_multiplier * 0.8)

    if snapshot.returned_after_low_sweep and has_oi_growth and has_volume:
        reasons = [
            "Sweep low 30m",
            "Цена вернулась в диапазон",
            f"OI +{oi_change:.2f}%",
            f"Volume {snapshot.volume_spike_ratio:.2f}x выше среднего",
        ]
        candidates.append(
            SignalCandidate(
                exchange=snapshot.exchange,
                symbol=snapshot.symbol,
                direction="LONG",
                pattern="stop_hunt_sweep",
                entry_price=snapshot.price,
                reasons=reasons,
                context=_base_context(snapshot),
            )
        )

    if snapshot.returned_after_high_sweep and has_oi_growth and has_volume:
        reasons = [
            "Sweep high 30m",
            "Цена вернулась в диапазон",
            f"OI +{oi_change:.2f}%",
            f"Volume {snapshot.volume_spike_ratio:.2f}x выше среднего",
        ]
        candidates.append(
            SignalCandidate(
                exchange=snapshot.exchange,
                symbol=snapshot.symbol,
                direction="SHORT",
                pattern="stop_hunt_sweep",
                entry_price=snapshot.price,
                reasons=reasons,
                context=_base_context(snapshot),
            )
        )
    return candidates

