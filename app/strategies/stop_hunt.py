from __future__ import annotations

from app.config import ThresholdsConfig
from app.market.features import FeatureSnapshot
from app.strategies.base import (
    StrategySignal,
    clamp_score,
    context_from_snapshot,
    invalidation_level,
    scale_points,
    spread_bonus,
)


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
        oi_change = market_state.oi_change_5m_pct
        if oi_change is None:
            oi_change = market_state.oi_change_15m_pct
        volume_floor = max(1.0, self.thresholds.volume_spike_multiplier * 0.8)
        if market_state.volume_spike_ratio < volume_floor:
            return None
        # OI is optional (candle-only backtests have no OI history), but a known
        # OI drop means positions are closing and the sweep setup is invalid.
        if oi_change is not None and oi_change < -0.5:
            return None
        if market_state.returned_after_low_sweep:
            direction = "LONG"
            reason = "Sweep low 30m"
        elif market_state.returned_after_high_sweep:
            direction = "SHORT"
            reason = "Sweep high 30m"
        else:
            return None
        score = clamp_score(
            5
            + scale_points(market_state.volume_spike_ratio, volume_floor, 2.0)
            + scale_points(oi_change, self.thresholds.oi_change_15m_pct / 2, 2.0)
            + spread_bonus(market_state.spread_pct)
        )
        oi_change = oi_change or 0.0
        return StrategySignal(
            exchange=market_state.exchange,
            symbol=market_state.symbol,
            direction=direction,
            strategy_key=self.key,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
            score=score,
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

