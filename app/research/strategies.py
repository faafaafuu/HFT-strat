"""Research strategies.

Each one is a hypothesis about where an edge could come from, written so the same harness
can run all of them. They decide on bar `index` from bars 0..index and the harness fills
on bar index+1, so nothing here can read a price the market had not printed yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.research.data import Series
from app.research.harness import Entry
from app.research.indicators import (
    adx,
    atr,
    ema,
    realised_volatility,
    rolling_max,
    rolling_min,
    rsi,
)


@dataclass
class DonchianBreakout:
    """Breakout of an N-bar range, stopped by ATR.

    Hypothesis: crypto trends persist after a range is resolved, and the persistence pays
    more than the fees, provided the stop is volatility-scaled rather than a fixed percent.
    """

    key: str = "donchian_breakout"
    lookback: int = 55
    atr_period: int = 14
    atr_mult: float = 2.5
    rr: float = 3.0
    trend_ema: int = 0
    min_adx: float = 0.0
    allow_short: bool = True
    warmup: int = 0
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.warmup = max(self.lookback, self.atr_period, self.trend_ema) + 5

    def prepare(self, series: Series) -> None:
        self._cache = {
            "highs": rolling_max(series.high, self.lookback),
            "lows": rolling_min(series.low, self.lookback),
            "atr": atr(series, self.atr_period),
            "ema": ema(series.close, self.trend_ema) if self.trend_ema else None,
            "adx": adx(series) if self.min_adx else None,
        }

    def signal(self, series: Series, index: int) -> Entry | None:
        cache = self._cache
        # The channel must exclude the current bar, otherwise "close above the highest
        # high including this one" is true on every new high and the test is vacuous.
        prior_high = cache["highs"][index - 1]
        prior_low = cache["lows"][index - 1]
        current_atr = cache["atr"][index]
        if prior_high is None or prior_low is None or not current_atr:
            return None
        if cache["adx"] is not None:
            strength = cache["adx"][index]
            if strength is None or strength < self.min_adx:
                return None
        close = series.close[index]
        trend = cache["ema"][index] if cache["ema"] is not None else None
        if self.trend_ema and trend is None:
            return None
        distance = current_atr * self.atr_mult
        if close > prior_high and (trend is None or close > trend):
            return Entry("LONG", close - distance, _take(True, close, distance, self.rr), "пробой вверх")
        if self.allow_short and close < prior_low and (trend is None or close < trend):
            return Entry("SHORT", close + distance, _take(False, close, distance, self.rr), "пробой вниз")
        return None


@dataclass
class TrendPullback:
    """Buy a pullback inside an established trend.

    Hypothesis: entering on weakness inside a trend gives a tighter stop than a breakout,
    so the same move pays more R and the fee share of the edge drops.
    """

    key: str = "trend_pullback"
    fast_ema: int = 20
    slow_ema: int = 100
    rsi_period: int = 14
    rsi_entry: float = 40.0
    atr_period: int = 14
    atr_mult: float = 2.0
    rr: float = 2.5
    allow_short: bool = True
    warmup: int = 0
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.warmup = max(self.slow_ema, self.rsi_period, self.atr_period) + 5

    def prepare(self, series: Series) -> None:
        self._cache = {
            "fast": ema(series.close, self.fast_ema),
            "slow": ema(series.close, self.slow_ema),
            "rsi": rsi(series.close, self.rsi_period),
            "atr": atr(series, self.atr_period),
        }

    def signal(self, series: Series, index: int) -> Entry | None:
        cache = self._cache
        fast, slow = cache["fast"][index], cache["slow"][index]
        strength, current_atr = cache["rsi"][index], cache["atr"][index]
        if fast is None or slow is None or strength is None or not current_atr:
            return None
        close = series.close[index]
        distance = current_atr * self.atr_mult
        uptrend = fast > slow and close > slow
        downtrend = fast < slow and close < slow
        if uptrend and strength <= self.rsi_entry:
            return Entry("LONG", close - distance, _take(True, close, distance, self.rr), "откат в тренде")
        if self.allow_short and downtrend and strength >= 100 - self.rsi_entry:
            return Entry("SHORT", close + distance, _take(False, close, distance, self.rr), "откат в тренде")
        return None


@dataclass
class RangeReversion:
    """Fade an extreme when the market is not trending.

    Hypothesis: the losing strategies traded every regime alike; mean reversion should be
    restricted to quiet, low-ADX stretches and nothing else.
    """

    key: str = "range_reversion"
    rsi_period: int = 14
    oversold: float = 25.0
    max_adx: float = 20.0
    atr_period: int = 14
    atr_mult: float = 1.5
    rr: float = 1.5
    allow_short: bool = True
    warmup: int = 0
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.warmup = max(self.rsi_period, self.atr_period, 30) + 5

    def prepare(self, series: Series) -> None:
        self._cache = {
            "rsi": rsi(series.close, self.rsi_period),
            "atr": atr(series, self.atr_period),
            "adx": adx(series),
        }

    def signal(self, series: Series, index: int) -> Entry | None:
        cache = self._cache
        strength, current_atr, trend = (
            cache["rsi"][index],
            cache["atr"][index],
            cache["adx"][index],
        )
        if strength is None or not current_atr or trend is None or trend > self.max_adx:
            return None
        close = series.close[index]
        distance = current_atr * self.atr_mult
        if strength <= self.oversold:
            return Entry("LONG", close - distance, _take(True, close, distance, self.rr), "перепроданность во флете")
        if self.allow_short and strength >= 100 - self.oversold:
            return Entry("SHORT", close + distance, _take(False, close, distance, self.rr), "перекупленность во флете")
        return None


@dataclass
class VolatilityBreakout:
    """Enter when a quiet stretch breaks: volatility expansion after compression.

    Hypothesis: the edge is not in direction but in timing — a move born out of the
    tightest range of the last N bars runs far enough to clear the costs.
    """

    key: str = "volatility_breakout"
    squeeze_lookback: int = 48
    squeeze_percentile: float = 0.25
    breakout_lookback: int = 12
    atr_period: int = 14
    atr_mult: float = 2.0
    rr: float = 3.0
    allow_short: bool = True
    warmup: int = 0
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.warmup = max(self.squeeze_lookback, self.atr_period) + 10

    def prepare(self, series: Series) -> None:
        self._cache = {
            "vol": realised_volatility(series.close, 24),
            "highs": rolling_max(series.high, self.breakout_lookback),
            "lows": rolling_min(series.low, self.breakout_lookback),
            "atr": atr(series, self.atr_period),
        }

    def signal(self, series: Series, index: int) -> Entry | None:
        cache = self._cache
        vol = cache["vol"][index]
        current_atr = cache["atr"][index]
        prior_high, prior_low = cache["highs"][index - 1], cache["lows"][index - 1]
        if vol is None or not current_atr or prior_high is None or prior_low is None:
            return None
        window = [
            value
            for value in cache["vol"][max(0, index - self.squeeze_lookback) : index + 1]
            if value is not None
        ]
        if len(window) < self.squeeze_lookback // 2:
            return None
        ranked = sorted(window)
        threshold = ranked[max(0, int(len(ranked) * self.squeeze_percentile) - 1)]
        if vol > threshold:
            return None
        close = series.close[index]
        distance = current_atr * self.atr_mult
        if close > prior_high:
            return Entry("LONG", close - distance, _take(True, close, distance, self.rr), "разжатие вверх")
        if self.allow_short and close < prior_low:
            return Entry("SHORT", close + distance, _take(False, close, distance, self.rr), "разжатие вниз")
        return None


def _take(long: bool, close: float, distance: float, rr: float) -> float | None:
    """rr <= 0 means "no fixed target": the trade is then closed by trailing or by time."""
    if rr <= 0:
        return None
    return close + distance * rr if long else close - distance * rr


BUILDERS = {
    "donchian_breakout": DonchianBreakout,
    "trend_pullback": TrendPullback,
    "range_reversion": RangeReversion,
    "volatility_breakout": VolatilityBreakout,
}


def build(key: str, **params: Any) -> Any:
    builder = BUILDERS.get(key)
    if builder is None:
        raise ValueError(f"Неизвестная исследовательская стратегия: {key}")
    return builder(**params)
