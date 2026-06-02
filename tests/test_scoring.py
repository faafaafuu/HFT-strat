from app.config import ThresholdsConfig
from app.signals.patterns import SignalCandidate
from app.signals.scoring import score_signal


def test_score_caps_at_ten_for_strong_context() -> None:
    thresholds = ThresholdsConfig()
    candidate = SignalCandidate(
        exchange="bybit",
        symbol="SOLUSDT",
        direction="LONG",
        pattern="stop_hunt_sweep",
        entry_price=100,
        reasons=[],
        context={
            "oi_change_15m_pct": 4.0,
            "swept_low_30m": 99.0,
            "volume_spike_ratio": 2.2,
            "spread_pct": 0.01,
            "funding_rate_pct": 0.05,
            "liquidation_usd_5m": 1_000_000,
            "price_change_5m_pct": 1.2,
        },
    )

    assert score_signal(candidate, thresholds) == 10


def test_score_for_basic_oi_price_move() -> None:
    thresholds = ThresholdsConfig()
    candidate = SignalCandidate(
        exchange="bybit",
        symbol="ETHUSDT",
        direction="SHORT",
        pattern="oi_pump_price_move",
        entry_price=100,
        reasons=[],
        context={
            "oi_change_15m_pct": 2.5,
            "volume_spike_ratio": 1.6,
            "spread_pct": 0.02,
            "price_change_5m_pct": -0.9,
        },
    )

    assert score_signal(candidate, thresholds) == 5

