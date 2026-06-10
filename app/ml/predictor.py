from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MLPredictor:
    def __init__(self, model_path: str = "/app/models/active_model.json") -> None:
        self.model_path = Path(model_path)
        self._model = self._load()

    @property
    def active(self) -> bool:
        return bool(self._model and self._model.get("is_active"))

    def quality_score(self, context: dict[str, Any]) -> float | None:
        if not self.active:
            return None
        weights = self._model.get("weights") or {}
        density = context.get("density_event") or {}
        trend = context.get("trend_context") or {}
        features = {
            "score": context.get("score", 0.0),
            "trend_alignment_score": trend.get("trend_alignment_score", 0.0),
            "price_change_5m": context.get("price_change_5m_pct", 0.0),
            "oi_change_15m": context.get("oi_change_15m_pct", 0.0),
            "volume_spike": context.get("volume_spike_ratio", 0.0),
            "spread_pct": context.get("spread_pct", 0.0),
            "density_size_usd": density.get("size_usd", 0.0),
            "density_lifetime_sec": density.get("lifetime_sec", 0.0),
            "density_distance_pct": density.get("distance_pct", 0.0),
            "absorption_score": density.get("absorption_score", 0.0),
            "spoof_score": density.get("spoof_score", 0.0),
            "funding_rate": context.get("funding_rate_pct", 0.0),
        }
        raw = 0.5
        for feature, weight in weights.items():
            value = float(features.get(feature, 0.0) or 0.0)
            raw += max(-0.05, min(0.05, value / 10 * float(weight or 0.0)))
        return max(0.0, min(1.0, raw))

    def adjustment(self, quality_score: float | None) -> float:
        if quality_score is None:
            return 0.0
        return max(-1.5, min(1.5, (quality_score - 0.5) * 3.0))

    def _load(self) -> dict[str, Any] | None:
        if not self.model_path.exists():
            return None
        try:
            value = json.loads(self.model_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
