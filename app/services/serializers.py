from __future__ import annotations

from typing import Any


def normalize_signal_summary(summary: dict[str, Any]) -> dict[str, Any]:
    result = dict(summary)
    for key in ("best_pattern", "best_pair", "worst_pair"):
        result[key] = normalize_stat_row(result.get(key))
    return result


def normalize_stat_row(row: object) -> tuple[str, float] | None:
    if not row:
        return None
    try:
        return str(row[0]), float(row[1])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        return None

