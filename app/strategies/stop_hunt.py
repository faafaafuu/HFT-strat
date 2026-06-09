from __future__ import annotations

from app.config import ThresholdsConfig
from app.market.features import FeatureSnapshot
from app.strategies.base import StrategySignal, context_from_snapshot, invalidation_level


class StopHuntSweepStrategy:
    key = "stop_hunt_sweep"
    name = "Stop Hunt / Liquidity Sweep"

    def __init__(self, thresholds: ThresholdsConfig) -> None:
        self.thresholds = thresholds

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
    ) -> StrategySignal | None:
        oi_change = market_state.oi_change_5m_pct or market_state.oi_change_15m_pct or 0.0
        has_oi_growth = oi_change >= self.thresholds.oi_change_15m_pct / 2
        has_volume = market_state.volume_spike_ratio >= max(
            1.0, self.thresholds.volume_spike_multiplier * 0.8
        )
        if not (has_oi_growth and has_volume):
            return None
        if market_state.returned_after_low_sweep:
            direction = "LONG"
            reason = "Sweep low 30m"
        elif market_state.returned_after_high_sweep:
            direction = "SHORT"
            reason = "Sweep high 30m"
        else:
            return None
        score = 5
        score += 2
        score += 1 if has_volume else 0
        score += 1 if has_oi_growth else 0
        score += 1 if market_state.spread_pct is not None and market_state.spread_pct <= 0.05 else 0
        return StrategySignal(
            exchange=market_state.exchange,
            symbol=market_state.symbol,
            direction=direction,
            strategy_key=self.key,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
            score=min(score, 10),
            reasons=[
                reason,
                "Цена вернулась в диапазон",
                f"OI +{oi_change:.2f}%",
                f"Volume {market_state.volume_spike_ratio:.2f}x выше среднего",
            ],
            entry_reference=market_state.price,
            invalidation_level=invalidation_level(direction, market_state.price, 0.5),
            suggested_stop_pct=0.5,
            suggested_take_pct=1.5,
            market_context=context_from_snapshot(market_state),
        )

