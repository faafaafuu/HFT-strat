from __future__ import annotations

from typing import Any

from app.config import DensityStrategyConfig
from app.market.features import FeatureSnapshot
from app.strategies.base import StrategySignal, context_from_snapshot, invalidation_level


class DensityStrategy:
    key = "density_strategy"
    name = "Orderbook Density Strategy"
    description = "Trades bounce, eaten density, spoof pull, and absorption around large L2 levels."

    def __init__(self, defaults: DensityStrategyConfig) -> None:
        self.defaults = defaults

    def default_config(self) -> dict[str, Any]:
        """Flat parameter names this strategy honours, with their current values."""
        return _merged_config(self.defaults, {})

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> StrategySignal | None:
        event = market_state.density_event
        if not event:
            return None
        cfg = _merged_config(self.defaults, config or {})
        if float(event.get("size_usd", 0.0)) < cfg["min_density_usd"]:
            return None
        if float(event.get("distance_pct", 999.0)) > cfg["max_distance_pct"]:
            return None
        if float(event.get("lifetime_sec", 0.0)) < cfg["min_lifetime_sec"]:
            return None
        if cfg["require_volume_spike"] and market_state.volume_spike_ratio < cfg["volume_spike_multiplier"]:
            return None
        trend = market_state.trend_context or {}
        trend_score = float(trend.get("trend_alignment_score", 0.0) or 0.0)
        event_type = str(event.get("event_type", ""))
        side = str(event.get("side", ""))
        direction, setup = _direction_for_event(event_type, side)
        if direction is None:
            return None
        if cfg["require_trend_alignment"] and not _trend_allows(direction, trend_score, cfg):
            return None
        absorption = float(event.get("absorption_score", 0.0) or 0.0)
        eaten = float(event.get("eaten_pct", 0.0) or 0.0)
        pulled = float(event.get("pulled_pct", 0.0) or 0.0)
        if cfg["require_absorption"] and absorption < 5 and setup not in {"density_breakout", "spoof_pull"}:
            return None
        score = 5
        score += 2 if setup in {"density_breakout", "absorption"} else 1
        score += 1 if market_state.volume_spike_ratio >= cfg["volume_spike_multiplier"] else 0
        score += 1 if _trend_allows(direction, trend_score, cfg) else 0
        score += min(2, absorption / 4) if absorption else 0
        score += min(1.5, eaten / 70) if eaten else 0
        score += min(1, pulled / 100) if pulled else 0
        if setup == "spoof_pull":
            score -= 1
        final_score = int(max(1, min(10, round(score))))
        stop_pct = float(cfg["stop_behind_density_pct"])
        take_pct = stop_pct * float(cfg["take_profit_rr"])
        context = context_from_snapshot(market_state)
        context["density_setup"] = setup
        context["density_event"] = event
        context["trend_context"] = trend
        reasons = [
            setup.replace("_", " ").title(),
            f"{side} density ${float(event.get('size_usd', 0.0)):,.0f}",
            f"Distance {float(event.get('distance_pct', 0.0)):.2f}%",
            f"Lifetime {float(event.get('lifetime_sec', 0.0)):.0f}s",
        ]
        if trend:
            reasons.append(f"Trend score {trend_score:+.1f}")
        return StrategySignal(
            exchange=market_state.exchange,
            symbol=market_state.symbol,
            direction=direction,
            strategy_key=self.key,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
            score=final_score,
            reasons=reasons,
            entry_reference=market_state.price,
            invalidation_level=invalidation_level(direction, market_state.price, stop_pct),
            suggested_stop_pct=stop_pct,
            suggested_take_pct=take_pct,
            market_context=context,
            confidence=min(0.95, max(0.1, final_score / 10)),
        )


def _merged_config(defaults: DensityStrategyConfig, override: dict[str, Any]) -> dict[str, Any]:
    base = {
        "min_density_usd": defaults.min_density_usd,
        "max_distance_pct": defaults.max_distance_pct,
        "min_lifetime_sec": defaults.min_lifetime_sec,
        "require_volume_spike": defaults.require_volume_spike,
        "volume_spike_multiplier": defaults.volume_spike_multiplier,
        "require_absorption": defaults.require_absorption,
        "require_trend_alignment": False,
        "min_trend_alignment_score": 0.0,
        "stop_behind_density_pct": defaults.risk.stop_behind_density_pct,
        "take_profit_rr": defaults.risk.take_profit_rr,
    }
    base.update(override)
    return base


def _direction_for_event(event_type: str, side: str) -> tuple[str | None, str]:
    if event_type in {"holding", "refilled"} and side == "bid":
        return "LONG", "density_bounce"
    if event_type in {"holding", "refilled"} and side == "ask":
        return "SHORT", "density_bounce"
    if event_type == "eaten" and side == "ask":
        return "LONG", "density_breakout"
    if event_type == "eaten" and side == "bid":
        return "SHORT", "density_breakout"
    if event_type == "pulled" and side == "ask":
        return "LONG", "spoof_pull"
    if event_type == "pulled" and side == "bid":
        return "SHORT", "spoof_pull"
    if event_type == "absorbed" and side == "bid":
        return "LONG", "absorption"
    if event_type == "absorbed" and side == "ask":
        return "SHORT", "absorption"
    return None, "unknown"


def _trend_allows(direction: str, trend_score: float, cfg: dict[str, Any]) -> bool:
    minimum = float(cfg.get("min_trend_alignment_score", 0.0) or 0.0)
    if direction == "LONG":
        return trend_score >= -minimum
    return trend_score <= minimum
