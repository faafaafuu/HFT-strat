from dataclasses import dataclass
from datetime import datetime, timedelta

from app.backtesting.simulator import ExitRules, simulate_exit

START = datetime(2026, 1, 1)


@dataclass(frozen=True)
class Bar:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float


def _bars(*shapes: tuple[float, float]) -> list[Bar]:
    """Each shape is (high, low); open/close sit in the middle."""
    return [
        Bar(
            open_time=START + timedelta(minutes=index),
            open=(high + low) / 2,
            high=high,
            low=low,
            close=(high + low) / 2,
        )
        for index, (high, low) in enumerate(shapes)
    ]


def _trade(bars: list[Bar], rules: ExitRules | None = None, **overrides):
    kwargs = {
        "direction": "LONG",
        "entry_time": START,
        "entry_price": 100.0,
        "future_candles": bars,
        "stop_pct": 1.0,
        "take_pct": 5.0,
        "max_holding_candles": 50,
        "position_size_usd": 1000.0,
        "taker_fee_pct": 0.0,
        "slippage_pct": 0.0,
        "rules": rules,
    }
    kwargs.update(overrides)
    return simulate_exit(**kwargs)


def test_without_rules_behaviour_is_unchanged() -> None:
    """Management is opt-in, so an old backtest must produce the old numbers."""
    bars = _bars((103.0, 99.5), (106.0, 102.0))

    plain = _trade(bars)
    explicit_off = _trade(bars, ExitRules())

    assert plain.status == "TP"
    assert plain.pnl_usd == explicit_off.pnl_usd
    assert plain.trailing_activated is False


def test_breakeven_turns_a_loser_into_a_flat_trade() -> None:
    """Price reaches +1R, comes back to entry: the stop should now sit at entry."""
    bars = _bars((101.5, 100.0), (100.5, 98.0))
    rules = ExitRules(breakeven_enabled=True, breakeven_activation_rr=1.0)

    without = _trade(bars)
    with_be = _trade(bars, rules)

    assert without.status == "SL"
    assert without.pnl_usd < 0
    assert with_be.status == "BE"
    assert with_be.exit_price == 100.0
    assert with_be.pnl_usd == 0.0


def test_trailing_locks_in_profit_when_price_reverses() -> None:
    bars = _bars((104.0, 100.0), (104.5, 101.0), (102.0, 95.0))
    rules = ExitRules(trailing_enabled=True, trailing_activation_rr=1.0, trailing_distance_pct=2.0)

    without = _trade(bars)
    with_trail = _trade(bars, rules)

    assert without.status == "SL"
    assert with_trail.trailing_activated is True
    # Trailed to 2% below the 104.5 high watermark, not down to the original 99.
    assert with_trail.exit_price > 100.0
    assert with_trail.pnl_usd > 0


def test_trailing_never_loosens_the_stop() -> None:
    """Later bars with lower highs must not drag the stop back down."""
    # Bar 1 sets the watermark at 104; the next two stay above the trailed stop but
    # peak lower, and the last one falls through it.
    bars = _bars((104.0, 100.0), (102.5, 102.0), (103.0, 102.2), (102.0, 95.0))
    rules = ExitRules(trailing_enabled=True, trailing_activation_rr=1.0, trailing_distance_pct=2.0)

    trade = _trade(bars, rules)

    assert trade.final_stop_price == 104.0 * 0.98
    assert trade.exit_price == 104.0 * 0.98
    assert trade.pnl_usd > 0


def test_partial_take_banks_profit_and_keeps_the_rest_running() -> None:
    bars = _bars((101.5, 100.0), (106.0, 101.0))
    rules = ExitRules(partial_tp_enabled=True, partial_tp_pct=50.0, partial_target_rr=1.0)

    without = _trade(bars)
    with_partial = _trade(bars, rules)

    assert with_partial.partial_exit_price == 101.0
    assert with_partial.partial_pnl_usd > 0
    assert with_partial.status == "TP"
    # Half the size rode to the 5% target instead of all of it.
    assert with_partial.pnl_usd < without.pnl_usd


def test_partial_take_fires_only_once() -> None:
    bars = _bars((101.5, 100.0), (101.6, 100.5), (106.0, 101.0))
    rules = ExitRules(partial_tp_enabled=True, partial_tp_pct=50.0, partial_target_rr=1.0)

    trade = _trade(bars, rules)
    full = _trade(bars)

    assert trade.partial_pnl_usd > 0
    # One partial at +1R on half the size, the rest at target: strictly less than all-in.
    assert trade.pnl_usd < full.pnl_usd


def test_short_side_trailing_and_breakeven() -> None:
    bars = _bars((100.0, 98.5), (101.5, 99.0))
    rules = ExitRules(breakeven_enabled=True, breakeven_activation_rr=1.0)

    trade = _trade(bars, rules, direction="SHORT")

    assert trade.status == "BE"
    assert trade.pnl_usd == 0.0


def test_fees_cover_every_leg() -> None:
    bars = _bars((101.5, 100.0), (106.0, 101.0))
    rules = ExitRules(partial_tp_enabled=True, partial_tp_pct=50.0, partial_target_rr=1.0)

    trade = _trade(bars, rules, taker_fee_pct=0.1)

    # Entry on the full size, then two exit legs summing to the full size.
    assert trade.fees_usd == 1000.0 * 0.001 * 2
