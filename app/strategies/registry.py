from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from app.config import Settings
from app.market.features import FeatureSnapshot
from app.strategies.base import Strategy, StrategySignal
from app.strategies.density_strategy import DensityStrategy
from app.strategies.liquidity_reclaim import (
    FailedBreakoutFadeStrategy,
    MicroStopHuntReclaimStrategy,
)
from app.strategies.oi_pump import OIMomentumScalperStrategy, OIPumpPriceMoveStrategy
from app.strategies.scalping_momentum import TrendPullbackScalperStrategy
from app.strategies.stop_hunt import StopHuntSweepStrategy


@dataclass(frozen=True)
class StrategyDescriptor:
    key: str
    name: str
    description: str
    enabled: bool
    profiles: list[str]
    instances: list[str]


class StrategyRegistry:
    def __init__(self, strategies: Iterable[Strategy]) -> None:
        self._strategies = {strategy.key: strategy for strategy in strategies}

    @classmethod
    def from_settings(cls, settings: Settings) -> StrategyRegistry:
        return cls(
            [
                OIPumpPriceMoveStrategy(settings.thresholds),
                StopHuntSweepStrategy(settings.thresholds),
                DensityStrategy(settings.density_strategy),
                MicroStopHuntReclaimStrategy(),
                OIMomentumScalperStrategy(),
                FailedBreakoutFadeStrategy(),
                TrendPullbackScalperStrategy(),
            ]
        )

    def keys(self) -> list[str]:
        return sorted(self._strategies)

    def get(self, key: str) -> Strategy | None:
        return self._strategies.get(key)

    def descriptors(self, settings: Settings) -> list[StrategyDescriptor]:
        profile_map: dict[str, list[str]] = {key: [] for key in self._strategies}
        instance_map: dict[str, list[str]] = {key: [] for key in self._strategies}
        for profile_key, profile in settings.strategy_profiles.profiles.items():
            for strategy_key in profile.strategies:
                if strategy_key in profile_map:
                    profile_map[strategy_key].append(profile_key)
        for instance_id, instance in settings.strategy_instances.instances.items():
            if instance.strategy_key in instance_map and instance.enabled:
                instance_map[instance.strategy_key].append(instance_id)
        return [
            StrategyDescriptor(
                key=key,
                name=strategy.name,
                description=getattr(strategy, "description", ""),
                enabled=settings.strategy_toggles.is_enabled(key)
                and bool(profile_map.get(key) or instance_map.get(key)),
                profiles=profile_map.get(key, []),
                instances=instance_map.get(key, []),
            )
            for key, strategy in sorted(self._strategies.items())
        ]

    def generate_signals(
        self,
        market_state: FeatureSnapshot,
        settings: Settings,
    ) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        used: set[tuple[str, str | None]] = set()
        for instance_id, instance in settings.strategy_instances.instances.items():
            if not instance.enabled:
                continue
            if instance.symbols != "auto" and market_state.symbol not in instance.symbols:
                continue
            if not settings.strategy_toggles.is_enabled(instance.strategy_key):
                continue
            strategy = self._strategies.get(instance.strategy_key)
            if strategy is None:
                continue
            candidate = _generate(
                strategy,
                market_state,
                strategy_profile_key=instance_id,
                paper_profile_key=instance.paper_profile,
                config=instance.config,
            )
            if candidate is None or candidate.score < instance.min_score:
                continue
            signals.append(
                replace(
                    candidate,
                    strategy_instance_id=instance_id,
                    strategy_profile_key=instance_id,
                    paper_profile_key=instance.paper_profile,
                    confidence=candidate.confidence or min(0.95, candidate.score / 10),
                )
            )
        for profile_key, profile in settings.strategy_profiles.profiles.items():
            if not profile.enabled:
                continue
            if profile.symbols != "auto" and market_state.symbol not in profile.symbols:
                continue
            for strategy_key in profile.strategies:
                if not settings.strategy_toggles.is_enabled(strategy_key):
                    continue
                dedupe_key = (strategy_key, profile.paper_profile)
                if dedupe_key in used:
                    continue
                strategy = self._strategies.get(strategy_key)
                if strategy is None:
                    continue
                candidate = _generate(
                    strategy,
                    market_state,
                    strategy_profile_key=profile_key,
                    paper_profile_key=profile.paper_profile,
                    config={},
                )
                if candidate is None:
                    continue
                if candidate.score < profile.min_score:
                    continue
                signals.append(candidate)
                used.add(dedupe_key)
        return signals


def default_registry(settings: Settings) -> StrategyRegistry:
    return StrategyRegistry.from_settings(settings)


def _generate(
    strategy: Strategy,
    market_state: FeatureSnapshot,
    *,
    strategy_profile_key: str | None,
    paper_profile_key: str | None,
    config: dict,
) -> StrategySignal | None:
    try:
        return strategy.generate_signal(
            market_state,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
            config=config,
        )
    except TypeError:
        return strategy.generate_signal(
            market_state,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
        )
