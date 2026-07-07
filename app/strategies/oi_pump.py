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


class OIPumpPriceMoveStrategy:
    key = "oi_pump_price_move"
    name = "OI Pump + Price Move"

    def __init__(self, thresholds: ThresholdsConfig) -> None:
        self.thresholds = thresholds

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
    ) -> StrategySignal | None:
        price_change = market_state.price_change_5m_pct
        oi_change = market_state.oi_change_15m_pct
        if price_change is None or oi_change is None:
            return None
        if abs(price_change) < self.thresholds.price_change_5m_pct:
            return None
        if oi_change < self.thresholds.oi_change_15m_pct:
            return None
        if market_state.volume_spike_ratio < self.thresholds.volume_spike_multiplier:
            return None
        if market_state.spread_pct is not None and market_state.spread_pct > 0.05:
            return None
        direction = "LONG" if price_change > 0 else "SHORT"
        # Base 5 for passing all filters; bonuses grow with the margin above each threshold.
        score = clamp_score(
            5
            + scale_points(abs(price_change), self.thresholds.price_change_5m_pct, 2.0)
            + scale_points(oi_change, self.thresholds.oi_change_15m_pct, 2.0)
            + scale_points(
                market_state.volume_spike_ratio, self.thresholds.volume_spike_multiplier, 1.0
            )
            + spread_bonus(market_state.spread_pct)
        )
        context = context_from_snapshot(market_state)
        return StrategySignal(
            exchange=market_state.exchange,
            symbol=market_state.symbol,
            direction=direction,
            strategy_key=self.key,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
            score=score,
            reasons=[
                f"Price {'up' if direction == 'LONG' else 'down'} {price_change:.2f}% за 5m",
                f"OI +{oi_change:.2f}% за 15m",
                f"Volume {market_state.volume_spike_ratio:.2f}x выше среднего",
            ],
            entry_reference=market_state.price,
            invalidation_level=invalidation_level(direction, market_state.price, 0.5),
            suggested_stop_pct=0.5,
            suggested_take_pct=1.5,
            market_context=context,
        )


class OIMomentumScalperStrategy:
    key = "oi_momentum_scalper"
    name = "OI Momentum Scalper"

    def __init__(
        self,
        price_change_3m_pct: float = 0.35,
        oi_change_10m_pct: float = 1.0,
        volume_spike: float = 1.5,
        max_spread_pct: float = 0.05,
    ) -> None:
        self.price_change_3m_pct = price_change_3m_pct
        self.oi_change_10m_pct = oi_change_10m_pct
        self.volume_spike = volume_spike
        self.max_spread_pct = max_spread_pct

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
    ) -> StrategySignal | None:
        price_change = market_state.price_change_5m_pct
        oi_change = market_state.oi_change_5m_pct or market_state.oi_change_15m_pct
        if price_change is None or oi_change is None:
            return None
        if abs(price_change) < self.price_change_3m_pct:
            return None
        if oi_change < self.oi_change_10m_pct:
            return None
        if market_state.volume_spike_ratio < self.volume_spike:
            return None
        if market_state.spread_pct is not None and market_state.spread_pct > self.max_spread_pct:
            return None
        direction = "LONG" if price_change > 0 else "SHORT"
        score = clamp_score(
            5
            + scale_points(abs(price_change), self.price_change_3m_pct, 2.0)
            + scale_points(oi_change, self.oi_change_10m_pct, 2.0)
            + scale_points(market_state.volume_spike_ratio, self.volume_spike, 1.0)
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
                f"Impulse {price_change:.2f}% за 5m",
                f"OI +{oi_change:.2f}%",
                f"Volume {market_state.volume_spike_ratio:.2f}x",
            ],
            entry_reference=market_state.price,
            invalidation_level=invalidation_level(direction, market_state.price, 0.45),
            suggested_stop_pct=0.45,
            suggested_take_pct=1.1,
            market_context=context_from_snapshot(market_state),
        )

