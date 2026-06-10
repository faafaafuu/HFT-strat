from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TrendContext:
    global_trend: str
    daily_trend: str
    local_trend: str
    trend_alignment_score: float
    volatility_regime: str

    def to_context(self) -> dict:
        return {
            "global_trend": self.global_trend,
            "daily_trend": self.daily_trend,
            "local_trend": self.local_trend,
            "trend_alignment_score": self.trend_alignment_score,
            "volatility_regime": self.volatility_regime,
        }


class TrendAnalyzer:
    def analyze(
        self,
        prices: list[tuple[datetime, float]],
        trades: list[tuple[datetime, str, float, float, float]],
    ) -> TrendContext:
        values = [price for _, price in prices]
        if len(values) < 10:
            return TrendContext("neutral", "neutral", "neutral", 0.0, "unknown")
        global_trend = _trend(values[-240:] if len(values) >= 240 else values)
        daily_trend = _trend(values[-120:] if len(values) >= 120 else values)
        local_trend = _trend(values[-30:] if len(values) >= 30 else values)
        score = _score(global_trend) * 0.5 + _score(daily_trend) + _score(local_trend) * 0.5
        volatility = _volatility(values[-60:] if len(values) >= 60 else values)
        return TrendContext(
            global_trend=global_trend,
            daily_trend=daily_trend,
            local_trend=local_trend,
            trend_alignment_score=max(-2.0, min(2.0, score)),
            volatility_regime=volatility,
        )


def _trend(values: list[float]) -> str:
    if len(values) < 5:
        return "neutral"
    first = values[0]
    last = values[-1]
    change_pct = (last - first) / first * 100 if first else 0.0
    highs_up = max(values[-max(3, len(values) // 3) :]) >= max(values[: max(3, len(values) // 3)])
    lows_up = min(values[-max(3, len(values) // 3) :]) >= min(values[: max(3, len(values) // 3)])
    if change_pct > 0.25 and highs_up and lows_up:
        return "bullish"
    if change_pct < -0.25 and not highs_up and not lows_up:
        return "bearish"
    return "neutral"


def _score(trend: str) -> float:
    if trend == "bullish":
        return 1.0
    if trend == "bearish":
        return -1.0
    return 0.0


def _volatility(values: list[float]) -> str:
    if len(values) < 5:
        return "unknown"
    returns = [
        abs((values[index] - values[index - 1]) / values[index - 1] * 100)
        for index in range(1, len(values))
        if values[index - 1]
    ]
    avg = sum(returns) / len(returns) if returns else 0.0
    if avg > 0.25:
        return "high"
    if avg < 0.05:
        return "low"
    return "normal"
