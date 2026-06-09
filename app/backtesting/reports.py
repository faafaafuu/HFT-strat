from __future__ import annotations

from typing import Any


def compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "total_trades",
        "winrate",
        "profit_factor",
        "expectancy",
        "max_drawdown",
        "net_pnl",
        "return_pct",
        "tp_hit_rate",
        "sl_hit_rate",
        "timeout_rate",
    ]
    return {key: metrics.get(key, 0) for key in keys}
