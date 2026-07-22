from __future__ import annotations

import math
import random
from collections.abc import Iterable
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


CHANNEL_SEARCH_SPACE: dict[str, list[Any]] = {
    # The 1-1.5% stop / 3-5% take defaults only fit channels several percent wide, which
    # in practice means H1 and up. The tighter values keep lower timeframes reachable.
    "stop_pct": [0.3, 0.5, 1.0, 1.5],
    "max_stop_pct": [1.5, 2.0],
    "take_pct": [1.5, 3.0, 4.0, 5.0],
    "min_rr": [1.5, 2.0, 3.0],
    "touch_tolerance_pct": [0.05, 0.1, 0.2],
    "breakout_buffer_pct": [0.0, 0.1, 0.3],
    "min_bars_between_points": [3, 5, 10],
    "max_bars_wait_touch": [30, 60, 120],
    "pivot_lookback": [2, 3, 5],
}

SEARCH_SPACES: dict[str, dict[str, list[Any]]] = {
    "density_strategy": DENSITY_SEARCH_SPACE,
    "channel_4_touch": CHANNEL_SEARCH_SPACE,
}


def search_space_for(strategy_key: str) -> dict[str, list[Any]]:
    return SEARCH_SPACES.get(strategy_key, DEFAULT_SEARCH_SPACE)


def grid(
    space: dict[str, list[Any]] | None = None,
    *,
    limit: int = 100,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Up to `limit` combinations from `space`.

    Truncating the full product would pin every slow-varying key to its first value,
    so an oversized space is sampled instead - deterministically, and without
    materialising millions of rows.
    """
    items = space or DEFAULT_SEARCH_SPACE
    keys = [key for key in items if items[key]]
    if not keys or limit <= 0:
        return []
    sizes = [len(items[key]) for key in keys]
    total = math.prod(sizes)
    if total <= limit:
        indexes: Iterable[int] = range(total)
    else:
        # Not security-sensitive: a seeded sampler keeps hyperopt runs reproducible.
        indexes = sorted(random.Random(seed).sample(range(total), limit))  # noqa: S311
    return [_combination(items, keys, sizes, index) for index in indexes]


def _combination(
    items: dict[str, list[Any]],
    keys: list[str],
    sizes: list[int],
    index: int,
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, size in zip(reversed(keys), reversed(sizes), strict=True):
        index, position = divmod(index, size)
        row[key] = items[key][position]
    return {key: row[key] for key in keys}
