from __future__ import annotations

from itertools import product
from typing import Any

DEFAULT_SEARCH_SPACE: dict[str, list[Any]] = {
    "min_score": [6, 7, 8],
    "stop_loss_pct": [0.4, 0.5, 0.7],
    "take_profit_pct": [1.0, 1.5, 2.0],
    "max_holding_candles": [60, 120, 180],
}


def grid(space: dict[str, list[Any]] | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
    items = space or DEFAULT_SEARCH_SPACE
    keys = list(items)
    rows = [dict(zip(keys, values, strict=True)) for values in product(*(items[key] for key in keys))]
    return rows[:limit]
