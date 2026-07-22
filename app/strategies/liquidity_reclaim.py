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


class MicroStopHuntReclaimStrategy:
    key = "micro_stop_hunt_reclaim"
    name = "Микро-возврат после съёма стопов"
    description = (
        "То же, что съём ликвидности, но на коротком окне в 15 минут и с меньшим "
        "проколом (от 0.15%). Требует возврата в диапазон за 3 минуты — быстрый "
        "скальперский вариант для ликвидных пар."
    )

    def __init__(
        self,
        lookback_minutes: int = 15,
        return_minutes: int = 3,
        min_price_sweep_pct: float = 0.15,
        min_volume_spike: float = 1.3,
        min_score: int = 7,
    ) -> None:
        self.lookback_minutes = lookback_minutes
        self.return_minutes = return_minutes
        self.min_price_sweep_pct = min_price_sweep_pct
        self.min_volume_spike = min_volume_spike
        self.min_score = min_score

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
    ) -> StrategySignal | None:
        if market_state.volume_spike_ratio < self.min_volume_spike:
            return None
        oi_change = market_state.oi_change_5m_pct
        if oi_change is None:
            oi_change = market_state.oi_change_15m_pct
        if market_state.returned_after_low_sweep:
            direction = "LONG"
            swept_level = market_state.swept_low_30m
            reason = "Micro low sweep reclaimed"
        elif market_state.returned_after_high_sweep:
            direction = "SHORT"
            swept_level = market_state.swept_high_30m
            reason = "Micro high sweep reclaimed"
        else:
            return None
        if swept_level is None:
            return None
        sweep_pct = abs((market_state.price - swept_level) / swept_level) * 100
        if sweep_pct < self.min_price_sweep_pct:
            return None
        if oi_change is not None and oi_change < -0.5:
            return None
        score = clamp_score(
            5
            + scale_points(sweep_pct, self.min_price_sweep_pct, 2.0)
            + scale_points(market_state.volume_spike_ratio, self.min_volume_spike, 2.0)
            + (0.5 if oi_change is not None and oi_change > 0 else 0.0)
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
                f"Reclaim distance {sweep_pct:.2f}%",
                f"Volume {market_state.volume_spike_ratio:.2f}x",
                f"OI {oi_change:+.2f}%",
            ],
            entry_reference=market_state.price,
            invalidation_level=invalidation_level(direction, market_state.price, 0.4),
            suggested_stop_pct=0.4,
            suggested_take_pct=1.2,
            market_context=context_from_snapshot(market_state),
        )


class FailedBreakoutFadeStrategy:
    key = "failed_breakout_fade"
    name = "Ложный пробой"
    description = (
        "Цена выходит за границу получасового диапазона минимум на 0.2%, но закрепиться "
        "не может и в течение 5 минут возвращается внутрь. Вход — в сторону, "
        "противоположную несостоявшемуся пробою."
    )

    def __init__(
        self,
        breakout_lookback_minutes: int = 30,
        fail_return_minutes: int = 5,
        min_fakeout_pct: float = 0.2,
    ) -> None:
        self.breakout_lookback_minutes = breakout_lookback_minutes
        self.fail_return_minutes = fail_return_minutes
        self.min_fakeout_pct = min_fakeout_pct

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
    ) -> StrategySignal | None:
        if market_state.returned_after_low_sweep and market_state.swept_low_30m:
            direction = "LONG"
            level = market_state.swept_low_30m
            reason = "Failed downside breakout"
        elif market_state.returned_after_high_sweep and market_state.swept_high_30m:
            direction = "SHORT"
            level = market_state.swept_high_30m
            reason = "Failed upside breakout"
        else:
            return None
        fakeout_pct = abs((market_state.price - level) / level) * 100
        if fakeout_pct < self.min_fakeout_pct:
            return None
        if market_state.volume_spike_ratio < 1.1:
            return None
        score = clamp_score(
            5
            + scale_points(fakeout_pct, self.min_fakeout_pct, 2.5)
            + scale_points(market_state.volume_spike_ratio, 1.1, 2.0)
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
                reason,
                "Price returned inside range",
                f"Fakeout {fakeout_pct:.2f}%",
                f"Volume {market_state.volume_spike_ratio:.2f}x",
            ],
            entry_reference=market_state.price,
            invalidation_level=invalidation_level(direction, market_state.price, 0.5),
            suggested_stop_pct=0.5,
            suggested_take_pct=1.3,
            market_context=context_from_snapshot(market_state),
        )
