from __future__ import annotations


def pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new - old) / old * 100


def pct_distance(reference: float, value: float) -> float:
    if reference == 0:
        return 0.0
    return abs(value - reference) / reference * 100


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

