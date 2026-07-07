from __future__ import annotations

from app.market.features import FeatureSnapshot
from app.strategies.base import (
    StrategySignal,
    clamp_score,
    context_from_snapshot,
    invalidation_level,
    scale_points,
    spread_bonus,
)


class TrendPullbackScalperStrategy:
    key = "trend_pullback_scalper"
    name = "Trend Pullback Scalper"

    def __init__(
        self,
        trend_window_minutes: int = 30,
        pullback_pct: float = 0.3,
        continuation_volume_multiplier: float = 1.2,
    ) -> None:
        self.trend_window_minutes = trend_window_minutes
        self.pullback_pct = pullback_pct
        self.continuation_volume_multiplier = continuation_volume_multiplier

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
    ) -> StrategySignal | None:
        price_change = market_state.price_change_5m_pct
        if price_change is None:
            return None
        if abs(price_change) < self.pullback_pct:
            return None
        if market_state.volume_spike_ratio < self.continuation_volume_multiplier:
            return None
        direction = "LONG" if price_change > 0 else "SHORT"
        score = clamp_score(
            5
            + scale_points(abs(price_change), self.pullback_pct, 2.5)
            + scale_points(
                market_state.volume_spike_ratio, self.continuation_volume_multiplier, 2.0
            )
            + spread_bonus(market_state.spread_pct)
        )
        return StrategySignal(
            exchange=market_state.exchange,
            symbol=market_state.symbol,
            direction=direction,
            strategy_key=self.key,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
            score=score,
            reasons=[
                f"Trend impulse {price_change:.2f}%",
                f"Continuation volume {market_state.volume_spike_ratio:.2f}x",
                "Pullback structure held",
            ],
            entry_reference=market_state.price,
            invalidation_level=invalidation_level(direction, market_state.price, 0.4),
            suggested_stop_pct=0.4,
            suggested_take_pct=1.0,
            market_context=context_from_snapshot(market_state),
        )

