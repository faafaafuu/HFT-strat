from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def ms_to_datetime(value: int | float) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def timeframe_id(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d_%H-%M")

