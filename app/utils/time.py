from __future__ import annotations

import re
from datetime import UTC, datetime

_TIMEFRAME_PATTERN = re.compile(r"^(\d+)([mhdw]?)$")
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080}


def utc_now() -> datetime:
    return datetime.now(UTC)


def timeframe_minutes(timeframe: str) -> int:
    """Minutes per candle for "5m" / "1h" / "1d". A bare number is minutes."""
    match = _TIMEFRAME_PATTERN.match(timeframe.strip().lower())
    if match is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return amount * _UNIT_MINUTES[match.group(2) or "m"]


def ms_to_datetime(value: int | float) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def timeframe_id(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d_%H-%M")
