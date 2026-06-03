from app.config import PaperConfig
from app.paper.risk import (
    apply_entry_slippage,
    apply_exit_slippage,
    calculate_paper_plan,
    fee_for_notional,
    pnl_for_exit,
)


def test_position_size_is_capped_by_risk_not_leverage() -> None:
    config = PaperConfig(
        initial_balance=2000,
        leverage=5,
        risk_per_trade_pct=0.5,
        stop_pct=0.5,
        take_pct=1.5,
        slippage_pct=0,
    )

    plan = calculate_paper_plan(balance=2000, signal_price=100, direction="LONG", config=config)

    assert plan.risk_usd == 10
    assert plan.position_size_usd == 2000
    assert plan.stop_price == 99.5
    assert round(plan.take_price, 4) == 101.5


def test_position_size_is_capped_by_leverage_when_risk_allows_more() -> None:
    config = PaperConfig(
        initial_balance=2000,
        leverage=2,
        risk_per_trade_pct=5,
        stop_pct=0.5,
        take_pct=1.5,
        slippage_pct=0,
    )

    plan = calculate_paper_plan(balance=2000, signal_price=100, direction="LONG", config=config)

    assert plan.position_size_usd == 4000
    assert plan.risk_usd == 20


def test_short_slippage_and_pnl_are_directional() -> None:
    entry = apply_entry_slippage(100, "SHORT", 0.01)
    exit_price = apply_exit_slippage(98, "SHORT", 0.01)
    pnl = pnl_for_exit("SHORT", entry, exit_price, 1000)

    assert entry == 99.99
    assert round(exit_price, 4) == 98.0098
    assert pnl > 0


def test_fee_for_notional() -> None:
    assert fee_for_notional(2000, 0.055) == 1.1


def test_default_auto_trade_min_score_is_seven() -> None:
    assert PaperConfig().auto_trade_min_score == 7
