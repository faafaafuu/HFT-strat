"""Indicator series computed once per candle series.

Every function returns a list the same length as the input, with `None` where there is not
enough history yet. Nothing here may look forward: value at index i uses bars 0..i only.
"""

from __future__ import annotations

from app.research.data import Series


def true_range(series: Series) -> list[float]:
    values = [series.high[0] - series.low[0]]
    for index in range(1, len(series)):
        previous_close = series.close[index - 1]
        values.append(
            max(
                series.high[index] - series.low[index],
                abs(series.high[index] - previous_close),
                abs(series.low[index] - previous_close),
            )
        )
    return values


def atr(series: Series, period: int = 14) -> list[float | None]:
    ranges = true_range(series)
    out: list[float | None] = [None] * len(series)
    if len(series) < period:
        return out
    average = sum(ranges[:period]) / period
    out[period - 1] = average
    for index in range(period, len(series)):
        # Wilder smoothing, the same one the classic ATR stop assumes.
        average = (average * (period - 1) + ranges[index]) / period
        out[index] = average
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    multiplier = 2 / (period + 1)
    previous = seed
    for index in range(period, len(values)):
        previous = (values[index] - previous) * multiplier + previous
        out[index] = previous
    return out


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for index in range(period, len(values)):
        running += values[index] - values[index - period]
        out[index] = running / period
    return out


def rolling_max(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        out[index] = max(values[index - period + 1 : index + 1])
    return out


def rolling_min(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        out[index] = min(values[index - period + 1 : index + 1])
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    average_gain = gains / period
    average_loss = losses / period
    out[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
        out[index] = _rsi_value(average_gain, average_loss)
    return out


def adx(series: Series, period: int = 14) -> list[float | None]:
    """Trend strength; the regime filter most of the hypotheses hang off."""
    length = len(series)
    out: list[float | None] = [None] * length
    if length < period * 2:
        return out
    ranges = true_range(series)
    plus_dm = [0.0] * length
    minus_dm = [0.0] * length
    for index in range(1, length):
        up = series.high[index] - series.high[index - 1]
        down = series.low[index - 1] - series.low[index]
        plus_dm[index] = up if up > down and up > 0 else 0.0
        minus_dm[index] = down if down > up and down > 0 else 0.0
    smooth_tr = sum(ranges[1 : period + 1])
    smooth_plus = sum(plus_dm[1 : period + 1])
    smooth_minus = sum(minus_dm[1 : period + 1])
    dx_values: list[float] = []
    for index in range(period + 1, length):
        smooth_tr = smooth_tr - smooth_tr / period + ranges[index]
        smooth_plus = smooth_plus - smooth_plus / period + plus_dm[index]
        smooth_minus = smooth_minus - smooth_minus / period + minus_dm[index]
        if smooth_tr <= 0:
            continue
        plus_di = 100 * smooth_plus / smooth_tr
        minus_di = 100 * smooth_minus / smooth_tr
        total = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / total if total else 0.0
        dx_values.append(dx)
        if len(dx_values) == period:
            out[index] = sum(dx_values) / period
        elif len(dx_values) > period:
            previous = out[index - 1]
            if previous is not None:
                out[index] = (previous * (period - 1) + dx) / period
    return out


def realised_volatility(values: list[float], period: int = 24) -> list[float | None]:
    """Standard deviation of bar-to-bar returns, in percent."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return out
    changes = [0.0] + [
        (values[index] / values[index - 1] - 1) * 100 if values[index - 1] else 0.0
        for index in range(1, len(values))
    ]
    for index in range(period, len(values)):
        window = changes[index - period + 1 : index + 1]
        mean = sum(window) / period
        out[index] = (sum((value - mean) ** 2 for value in window) / period) ** 0.5
    return out


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0
    strength = average_gain / average_loss
    return 100 - 100 / (1 + strength)
