from app.signals.outcome_tracker import calculate_outcome


def test_long_outcome_uses_max_as_mfe_and_min_as_mae() -> None:
    outcome = calculate_outcome(
        direction="LONG",
        entry_price=100,
        min_price=99.4,
        max_price=101.2,
        price_after=100.8,
        tp_levels_pct=[0.5, 1.0, 1.5],
        sl_levels_pct=[0.3, 0.5, 0.7],
    )

    assert round(outcome["mfe_pct"], 2) == 1.2
    assert round(outcome["mae_pct"], 2) == 0.6
    assert outcome["hits"]["tp_0_5"] is True
    assert outcome["hits"]["tp_1_0"] is True
    assert outcome["hits"]["tp_1_5"] is False
    assert outcome["hits"]["sl_0_5"] is True
    assert outcome["hits"]["sl_0_7"] is False


def test_short_outcome_reverses_favorable_and_adverse_moves() -> None:
    outcome = calculate_outcome(
        direction="SHORT",
        entry_price=100,
        min_price=98.8,
        max_price=100.6,
        price_after=99.1,
        tp_levels_pct=[0.5, 1.0, 1.5],
        sl_levels_pct=[0.3, 0.5, 0.7],
    )

    assert round(outcome["mfe_pct"], 2) == 1.2
    assert round(outcome["mae_pct"], 2) == 0.6
    assert outcome["hits"]["tp_1_0"] is True
    assert outcome["hits"]["tp_1_5"] is False
    assert outcome["hits"]["sl_0_5"] is True
    assert outcome["hits"]["sl_0_7"] is False
