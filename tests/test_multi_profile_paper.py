from types import SimpleNamespace

import pytest

from app.config import PaperConfig
from app.paper.manager import PaperTradeManager
from app.paper.risk import calculate_paper_plan


@pytest.mark.asyncio
async def test_open_for_signal_routes_signal_to_all_enabled_profiles(monkeypatch) -> None:
    manager = PaperTradeManager(database=SimpleNamespace(), config=PaperConfig())
    signal = SimpleNamespace(id=123, score=8)
    opened_profiles = []

    async def fake_open_from_signal(signal_arg, profile_key=None, profile_config=None):
        if not profile_config.enabled or signal_arg.score < profile_config.min_score:
            return None
        opened_profiles.append(profile_key)
        return SimpleNamespace(profile_key=profile_key)

    monkeypatch.setattr(manager, "open_from_signal", fake_open_from_signal)

    trades = await manager.open_for_signal(signal)

    assert [trade.profile_key for trade in trades] == ["conservative", "aggressive"]
    assert opened_profiles == ["conservative", "aggressive"]


@pytest.mark.asyncio
async def test_open_for_signal_skips_profiles_below_min_score(monkeypatch) -> None:
    manager = PaperTradeManager(database=SimpleNamespace(), config=PaperConfig())
    signal = SimpleNamespace(id=124, score=7)

    async def fake_open_from_signal(signal_arg, profile_key=None, profile_config=None):
        if not profile_config.enabled or signal_arg.score < profile_config.min_score:
            return None
        return SimpleNamespace(profile_key=profile_key)

    monkeypatch.setattr(manager, "open_from_signal", fake_open_from_signal)

    trades = await manager.open_for_signal(signal)

    assert [trade.profile_key for trade in trades] == ["aggressive"]


def test_profile_specific_risk_settings_change_position_plan() -> None:
    config = PaperConfig()
    conservative = config.profiles["conservative"]
    aggressive = config.profiles["aggressive"]

    conservative_plan = calculate_paper_plan(
        balance=conservative.initial_balance,
        signal_price=100,
        direction="LONG",
        config=SimpleNamespace(
            leverage=conservative.leverage,
            risk_per_trade_pct=conservative.risk_per_trade_pct,
            stop_pct=conservative.stop_loss_pct,
            take_pct=conservative.take_profit_pct,
            slippage_pct=config.slippage_pct,
        ),
    )
    aggressive_plan = calculate_paper_plan(
        balance=aggressive.initial_balance,
        signal_price=100,
        direction="LONG",
        config=SimpleNamespace(
            leverage=aggressive.leverage,
            risk_per_trade_pct=aggressive.risk_per_trade_pct,
            stop_pct=aggressive.stop_loss_pct,
            take_pct=aggressive.take_profit_pct,
            slippage_pct=config.slippage_pct,
        ),
    )

    assert conservative_plan.risk_usd == 6
    assert aggressive_plan.risk_usd == 14
    assert conservative_plan.take_price < aggressive_plan.take_price
