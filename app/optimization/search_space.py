from __future__ import annotations

from itertools import product
from typing import Any

DEFAULT_SEARCH_SPACE: dict[str, list[Any]] = {
    "min_score": [6, 7, 8],
    "stop_loss_pct": [0.4, 0.5, 0.7],
    "take_profit_pct": [1.0, 1.5, 2.0],
    "max_holding_candles": [60, 120, 180],
}

DENSITY_SEARCH_SPACE: dict[str, list[Any]] = {
    "min_score": [7, 8, 9],
    "min_density_usd": [500_000, 1_000_000, 1_500_000],
    "max_distance_pct": [0.25, 0.35, 0.5],
    "min_lifetime_sec": [8, 15, 25],
    "pull_threshold_pct": [60, 70, 80],
    "eaten_threshold_pct": [60, 70, 80],
    "absorption_min_trades_usd": [100_000, 200_000, 400_000],
    "absorption_price_move_max_pct": [0.05, 0.08, 0.12],
    "stop_behind_density_pct": [0.12, 0.15, 0.25],
    "take_profit_rr": [1.5, 2.0, 2.5],
    "max_holding_minutes": [30, 60, 120],
    "require_trend_alignment": [False, True],
    "min_trend_alignment_score": [0.0, 0.5, 1.0],
}


def grid(space: dict[str, list[Any]] | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
    items = space or DEFAULT_SEARCH_SPACE
    keys = list(items)
    rows = [dict(zip(keys, values, strict=True)) for values in product(*(items[key] for key in keys))]
    return rows[:limit]
