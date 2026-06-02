from __future__ import annotations

from app.config import ThresholdsConfig
from app.signals.patterns import SignalCandidate


def score_signal(candidate: SignalCandidate, thresholds: ThresholdsConfig) -> int:
    ctx = candidate.context
    score = 0

    oi_change = ctx.get("oi_change_15m_pct") or ctx.get("oi_change_5m_pct") or 0
    if oi_change > thresholds.oi_change_15m_pct:
        score += 2

    if ctx.get("swept_low_30m") is not None or ctx.get("swept_high_30m") is not None:
        score += 2

    if (ctx.get("volume_spike_ratio") or 0) >= thresholds.volume_spike_multiplier:
        score += 1

    spread_pct = ctx.get("spread_pct")
    if spread_pct is not None and spread_pct <= 0.05:
        score += 1

    funding_rate = ctx.get("funding_rate_pct")
    if funding_rate is not None and abs(funding_rate) >= thresholds.funding_extreme_pct:
        score += 1

    if "liquidation_usd_5m" in ctx and (ctx.get("liquidation_usd_5m") or 0) > 0:
        score += 2

    if candidate.pattern == "stop_hunt_sweep":
        score += 1

    price_change = abs(ctx.get("price_change_5m_pct") or 0)
    if price_change >= thresholds.price_change_5m_pct:
        score += 1

    return min(score, 10)
