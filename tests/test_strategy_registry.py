from datetime import datetime

from app.config import Settings
from app.market.features import FeatureSnapshot
from app.strategies.registry import default_registry


def _snapshot(**overrides):
    data = {
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timestamp": datetime(2026, 1, 1),
        "price": 100.0,
        "price_change_5m_pct": 1.0,
        "volume_1m_usd": 100_000,
        "volume_5m_usd": 1_000_000,
        "avg_volume_5m_usd": 500_000,
        "volume_spike_ratio": 2.0,
        "oi": 10_000,
        "oi_change_5m_pct": 1.2,
        "oi_change_15m_pct": 2.5,
        "funding_rate_pct": 0.01,
        "spread_pct": 0.01,
        "bid_depth_1pct": 1_000_000,
        "ask_depth_1pct": 1_000_000,
        "swept_low_30m": None,
        "swept_high_30m": None,
        "returned_after_low_sweep": False,
        "returned_after_high_sweep": False,
    }
    data.update(overrides)
    return FeatureSnapshot(**data)


def test_registry_contains_scalping_strategies() -> None:
    registry = default_registry(Settings())

    assert "micro_stop_hunt_reclaim" in registry.keys()
    assert "oi_momentum_scalper" in registry.keys()
    assert "trend_pullback_scalper" in registry.keys()


def test_strategy_registry_generates_oi_signal() -> None:
    settings = Settings()
    registry = default_registry(settings)

    signals = registry.generate_signals(_snapshot(), settings)

    assert any(signal.strategy_key == "oi_pump_price_move" for signal in signals)
    assert all(signal.score >= settings.signals.min_score for signal in signals)
