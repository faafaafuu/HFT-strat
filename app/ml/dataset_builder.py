from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import SignalModel, SignalOutcomeModel

FEATURES = [
    "score",
    "trend_alignment_score",
    "price_change_5m",
    "oi_change_15m",
    "volume_spike",
    "spread_pct",
    "density_size_usd",
    "density_lifetime_sec",
    "density_distance_pct",
    "absorption_score",
    "spoof_score",
    "funding_rate",
]


async def build_signal_dataset(session: AsyncSession) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.execute(
                select(SignalModel, SignalOutcomeModel)
                .join(SignalOutcomeModel, SignalOutcomeModel.signal_id == SignalModel.id)
                .where(SignalOutcomeModel.horizon_minutes == 30)
            )
        ).all()
    )
    dataset = []
    for signal, outcome in rows:
        context = _json(signal.market_context_json)
        density = context.get("density_event") or {}
        trend = context.get("trend_context") or {}
        features = {
            "strategy_key": signal.strategy_key or signal.pattern,
            "symbol": signal.symbol,
            "hour_of_day": signal.timestamp.hour,
            "day_of_week": signal.timestamp.weekday(),
            "score": signal.score,
            "trend_alignment_score": float(trend.get("trend_alignment_score", 0.0) or 0.0),
            "price_change_5m": float(context.get("price_change_5m_pct", 0.0) or 0.0),
            "oi_change_15m": float(context.get("oi_change_15m_pct", 0.0) or 0.0),
            "volume_spike": float(context.get("volume_spike_ratio", 0.0) or 0.0),
            "spread_pct": float(context.get("spread_pct", 0.0) or 0.0),
            "density_size_usd": float(density.get("size_usd", 0.0) or 0.0),
            "density_lifetime_sec": float(density.get("lifetime_sec", 0.0) or 0.0),
            "density_distance_pct": float(density.get("distance_pct", 0.0) or 0.0),
            "absorption_score": float(density.get("absorption_score", 0.0) or 0.0),
            "spoof_score": float(density.get("spoof_score", 0.0) or 0.0),
            "funding_rate": float(context.get("funding_rate_pct", 0.0) or 0.0),
        }
        dataset.append(
            {
                "features": features,
                "target": {
                    "hit_tp_before_sl": bool(outcome.hit_tp_1_0 and not outcome.hit_sl_0_5),
                    "r_multiple_positive": bool(outcome.mfe_pct > outcome.mae_pct),
                    "future_return_30m_positive": bool(
                        outcome.price_after > signal.entry_price
                        if signal.direction == "LONG"
                        else outcome.price_after < signal.entry_price
                    ),
                },
            }
        )
    return dataset


def _json(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
