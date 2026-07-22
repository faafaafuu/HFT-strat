"""4-touch channel strategy.

After an impulse the price consolidates inside a parallel channel. Three pivots fix
that channel: two on one boundary (points 1 and 3) and one on the opposite boundary
(point 2). The second touch of the anchor line is what proves the line is real rather
than accidental, so the first touch of the *opposite* boundary after point 3 - the
fourth touch overall - is the entry.

Once built, the channel is a constant: boundaries never widen or narrow. Only two
things can happen afterwards - price rejects the boundary (a trade), or a candle
closes through it (the channel is dead and produces no further signals).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import ChannelStrategyConfig
from app.market.features import CandleBar, FeatureSnapshot
from app.strategies.base import (
    StrategySignal,
    clamp_score,
    context_from_snapshot,
    scale_points,
    spread_bonus,
)

PIVOT_HIGH = "high"
PIVOT_LOW = "low"


@dataclass(frozen=True)
class Pivot:
    index: int
    kind: str
    price: float


@dataclass(frozen=True)
class Channel:
    """Two parallel lines fixed by points 1-2-3, in window-relative indexes."""

    # "upper" when points 1 and 3 are highs (the anchor line is the channel top).
    anchor_side: str
    anchor_index: int
    anchor_price: float
    slope: float
    # Signed gap from the anchor line to the parallel line through point 2.
    offset: float
    point_indexes: tuple[int, int, int]

    def anchor_at(self, index: int) -> float:
        return self.anchor_price + self.slope * (index - self.anchor_index)

    def parallel_at(self, index: int) -> float:
        return self.anchor_at(index) + self.offset

    def upper_at(self, index: int) -> float:
        return self.anchor_at(index) if self.anchor_side == "upper" else self.parallel_at(index)

    def lower_at(self, index: int) -> float:
        return self.parallel_at(index) if self.anchor_side == "upper" else self.anchor_at(index)

    @property
    def target_side(self) -> str:
        """The boundary the 4th touch is expected on - the one opposite point 3."""
        return "lower" if self.anchor_side == "upper" else "upper"

    @property
    def direction(self) -> str:
        return "LONG" if self.target_side == "lower" else "SHORT"

    def boundary_at(self, side: str, index: int) -> float:
        return self.upper_at(index) if side == "upper" else self.lower_at(index)


class ChannelTouchStrategy:
    key = "channel_4_touch"
    name = "Канал: вход на 4-м касании"
    description = (
        "Строит параллельный канал по трём точкам: две на одной границе (1 и 3) и одна "
        "на противоположной (2). Второе касание опорной линии доказывает, что она реальна, "
        "поэтому вход — на первом касании противоположной границы после точки 3, "
        "то есть на четвёртом касании. Закрытие свечи за границей канала убивает сетап."
    )

    def __init__(self, defaults: ChannelStrategyConfig | None = None) -> None:
        self.defaults = defaults or ChannelStrategyConfig()

    @property
    def required_history(self) -> int:
        """Candles the backtest engine must hand over for the channel to be findable."""
        minimum = (
            self.defaults.pivot_lookback * 2
            + self.defaults.min_bars_between_points * 2
            + self.defaults.max_bars_wait_touch
            + 40
        )
        return max(self.defaults.history_candles, minimum)

    def default_config(self) -> dict[str, Any]:
        """The flat parameter names this strategy honours, with their current values.

        Comes from the same merge the signal path uses, so an editor built from it
        cannot drift away from what actually takes effect.
        """
        return _merged_config(self.defaults, {})

    @property
    def default_holding_candles(self) -> int:
        """A minutes-based holding cap would be 3 candles on H1 - far too short."""
        return self.defaults.max_holding_candles

    def generate_signal(
        self,
        market_state: FeatureSnapshot,
        *,
        strategy_profile_key: str | None = None,
        paper_profile_key: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> StrategySignal | None:
        cfg = _merged_config(self.defaults, config or {})
        candles = market_state.candles
        if len(candles) < cfg["pivot_lookback"] * 2 + cfg["min_bars_between_points"] * 2 + 3:
            return None
        current = len(candles) - 1
        channel = _find_channel(candles, current, cfg)
        if channel is None:
            return None
        touch = _touch_at(candles, current, channel, cfg)
        if touch is None:
            return None
        return _build_signal(
            market_state=market_state,
            candles=candles,
            current=current,
            channel=channel,
            touch=touch,
            cfg=cfg,
            strategy_key=self.key,
            strategy_profile_key=strategy_profile_key,
            paper_profile_key=paper_profile_key,
        )


def _merged_config(defaults: ChannelStrategyConfig, override: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pivot_lookback": defaults.pivot_lookback,
        "min_bars_between_points": defaults.min_bars_between_points,
        "min_bars_before_touch": defaults.min_bars_before_touch,
        "max_bars_wait_touch": defaults.max_bars_wait_touch,
        "touch_tolerance_pct": defaults.touch_tolerance_pct,
        "breakout_buffer_pct": defaults.breakout_buffer_pct,
        "stop_pct": defaults.stop_pct,
        "max_stop_pct": defaults.max_stop_pct,
        "stop_buffer_pct": defaults.stop_buffer_pct,
        "take_pct": defaults.take_pct,
        "min_rr": defaults.min_rr,
        "history_candles": defaults.history_candles,
    }
    base.update({key: value for key, value in override.items() if key in base})
    return base


def _pivots(candles: tuple[CandleBar, ...], lookback: int, last_confirmed: int) -> list[Pivot]:
    """Swing points that dominate `lookback` bars on both sides, oldest first."""
    pivots: list[Pivot] = []
    for index in range(lookback, last_confirmed + 1):
        window = candles[index - lookback : index + lookback + 1]
        candle = candles[index]
        if candle.high >= max(bar.high for bar in window):
            pivots.append(Pivot(index=index, kind=PIVOT_HIGH, price=candle.high))
        if candle.low <= min(bar.low for bar in window):
            pivots.append(Pivot(index=index, kind=PIVOT_LOW, price=candle.low))
    pivots.sort(key=lambda pivot: pivot.index)
    return pivots


def _find_channel(
    candles: tuple[CandleBar, ...],
    current: int,
    cfg: dict[str, Any],
) -> Channel | None:
    """Most recent channel whose points 1-2-3 are confirmed and still unbroken."""
    lookback = int(cfg["pivot_lookback"])
    gap = int(cfg["min_bars_between_points"])
    # A pivot is only real once `lookback` bars have printed to its right, so the
    # newest usable point 3 sits that far back - no lookahead.
    last_confirmed = current - lookback
    if last_confirmed < lookback:
        return None
    pivots = _pivots(candles, lookback, last_confirmed)
    if len(pivots) < 3:
        return None
    oldest_allowed = current - int(cfg["max_bars_wait_touch"])
    for position in range(len(pivots) - 1, -1, -1):
        point3 = pivots[position]
        if point3.index < oldest_allowed:
            break
        opposite = PIVOT_LOW if point3.kind == PIVOT_HIGH else PIVOT_HIGH
        point2 = _previous_pivot(pivots, position, opposite, point3.index - gap)
        if point2 is None:
            continue
        point1 = _previous_pivot(pivots, position, point3.kind, point2.index - gap)
        if point1 is None:
            continue
        channel = _channel_from_points(point1, point2, point3)
        if channel is None:
            continue
        if _is_broken(candles, channel, point1.index, current, cfg):
            continue
        return channel
    return None


def _previous_pivot(
    pivots: list[Pivot],
    before_position: int,
    kind: str,
    max_index: int,
) -> Pivot | None:
    for position in range(before_position - 1, -1, -1):
        pivot = pivots[position]
        if pivot.kind == kind and pivot.index <= max_index:
            return pivot
    return None


def _channel_from_points(point1: Pivot, point2: Pivot, point3: Pivot) -> Channel | None:
    span = point3.index - point1.index
    if span <= 0:
        return None
    slope = (point3.price - point1.price) / span
    anchor_at_point2 = point1.price + slope * (point2.index - point1.index)
    offset = point2.price - anchor_at_point2
    anchor_side = "upper" if point3.kind == PIVOT_HIGH else "lower"
    # Point 2 has to fall on the far side of the anchor line, otherwise these three
    # pivots do not enclose anything.
    if anchor_side == "upper" and offset >= 0:
        return None
    if anchor_side == "lower" and offset <= 0:
        return None
    return Channel(
        anchor_side=anchor_side,
        anchor_index=point1.index,
        anchor_price=point1.price,
        slope=slope,
        offset=offset,
        point_indexes=(point1.index, point2.index, point3.index),
    )


def _is_broken(
    candles: tuple[CandleBar, ...],
    channel: Channel,
    start: int,
    end: int,
    cfg: dict[str, Any],
) -> bool:
    """True once any candle *closes* past a boundary by more than the buffer."""
    buffer_pct = float(cfg["breakout_buffer_pct"]) / 100
    for index in range(start, end + 1):
        candle = candles[index]
        upper = channel.upper_at(index)
        lower = channel.lower_at(index)
        if candle.close > upper * (1 + buffer_pct):
            return True
        if candle.close < lower * (1 - buffer_pct):
            return True
    return False


@dataclass(frozen=True)
class Touch:
    """A wick reaching a boundary while the candle still closes inside the channel."""

    index: int
    boundary: float
    gap_pct: float
    pierced: bool


def _touch_of(
    candles: tuple[CandleBar, ...],
    index: int,
    channel: Channel,
    cfg: dict[str, Any],
) -> Touch | None:
    candle = candles[index]
    boundary = channel.boundary_at(channel.target_side, index)
    if boundary <= 0:
        return None
    tolerance = float(cfg["touch_tolerance_pct"]) / 100 * boundary
    # Same buffer as _is_broken: only a decisive close past the line is a breakout,
    # so anything short of that is still a wick touch.
    buffer = float(cfg["breakout_buffer_pct"]) / 100
    if channel.target_side == "lower":
        extreme = candle.low
        reached = extreme <= boundary + tolerance
        pierced = extreme < boundary
        # A wick counts, a close through the line does not.
        closed_inside = candle.close >= boundary * (1 - buffer)
    else:
        extreme = candle.high
        reached = extreme >= boundary - tolerance
        pierced = extreme > boundary
        closed_inside = candle.close <= boundary * (1 + buffer)
    if not reached or not closed_inside:
        return None
    return Touch(
        index=index,
        boundary=boundary,
        gap_pct=abs(extreme - boundary) / boundary * 100,
        pierced=pierced,
    )


def _touch_at(
    candles: tuple[CandleBar, ...],
    current: int,
    channel: Channel,
    cfg: dict[str, Any],
) -> Touch | None:
    """The 4th touch, and only the 4th: earlier touches after point 3 disqualify it."""
    point3_index = channel.point_indexes[2]
    if current - point3_index < int(cfg["min_bars_before_touch"]):
        return None
    touch = _touch_of(candles, current, channel, cfg)
    if touch is None:
        return None
    for index in range(point3_index + 1, current):
        if _touch_of(candles, index, channel, cfg) is not None:
            return None
    return touch


def _build_signal(
    *,
    market_state: FeatureSnapshot,
    candles: tuple[CandleBar, ...],
    current: int,
    channel: Channel,
    touch: Touch,
    cfg: dict[str, Any],
    strategy_key: str,
    strategy_profile_key: str | None,
    paper_profile_key: str | None,
) -> StrategySignal | None:
    candle = candles[current]
    entry = candle.close
    if entry <= 0:
        return None
    direction = channel.direction
    # The stop clears the touch wick and the boundary itself, but is never tighter
    # than the configured floor.
    if direction == "LONG":
        beyond = min(candle.low, touch.boundary) * (1 - float(cfg["stop_buffer_pct"]) / 100)
        structural_stop_pct = (entry - beyond) / entry * 100
        target = channel.upper_at(current)
        boundary_pct = (target - entry) / entry * 100
    else:
        beyond = max(candle.high, touch.boundary) * (1 + float(cfg["stop_buffer_pct"]) / 100)
        structural_stop_pct = (beyond - entry) / entry * 100
        target = channel.lower_at(current)
        boundary_pct = (entry - target) / entry * 100
    stop_pct = max(float(cfg["stop_pct"]), structural_stop_pct)
    if stop_pct <= 0 or stop_pct > float(cfg["max_stop_pct"]):
        return None
    if boundary_pct <= 0:
        return None
    # Fixed target or the opposite boundary, whichever comes first.
    take_pct = min(float(cfg["take_pct"]), boundary_pct)
    rr = take_pct / stop_pct
    if rr < float(cfg["min_rr"]):
        return None
    channel_width_pct = abs(channel.offset) / entry * 100
    score = clamp_score(
        5
        + scale_points(rr, float(cfg["min_rr"]), 2.0)
        + _precision_bonus(touch, cfg)
        + _rejection_bonus(candle, direction)
        + spread_bonus(market_state.spread_pct)
    )
    context = context_from_snapshot(market_state)
    context["channel"] = {
        "direction": direction,
        "anchor_side": channel.anchor_side,
        "touch_side": channel.target_side,
        "upper": channel.upper_at(current),
        "lower": channel.lower_at(current),
        "slope_pct_per_candle": channel.slope / entry * 100,
        "width_pct": channel_width_pct,
        "point_bars_ago": [current - index for index in channel.point_indexes],
        "bars_since_point3": current - channel.point_indexes[2],
        "touch_gap_pct": touch.gap_pct,
        "touch_pierced": touch.pierced,
        "rr": rr,
        "target_is_boundary": take_pct < float(cfg["take_pct"]),
    }
    slope_label = "rising" if channel.slope > 0 else "falling" if channel.slope < 0 else "flat"
    return StrategySignal(
        exchange=market_state.exchange,
        symbol=market_state.symbol,
        direction=direction,
        strategy_key=strategy_key,
        strategy_profile_key=strategy_profile_key,
        paper_profile_key=paper_profile_key,
        score=score,
        reasons=[
            f"4th touch of {channel.target_side} boundary",
            f"{slope_label.title()} channel confirmed by 3 pivots",
            f"Channel width {channel_width_pct:.2f}%",
            f"Touch {'pierced' if touch.pierced else 'held'} by {touch.gap_pct:.3f}%",
            f"R:R {rr:.2f} (stop {stop_pct:.2f}% / take {take_pct:.2f}%)",
        ],
        entry_reference=entry,
        invalidation_level=touch.boundary,
        suggested_stop_pct=stop_pct,
        suggested_take_pct=take_pct,
        market_context=context,
        confidence=min(0.95, max(0.1, score / 10)),
    )


def _precision_bonus(touch: Touch, cfg: dict[str, Any]) -> float:
    """Best when the wick lands on the line, whether it stops short or runs through.

    A deep pierce is not a cleaner touch than a shallow one - on 4h BTC/ETH/SOL the
    pierced touches actually won less often - so distance from the line is scored the
    same in both directions.
    """
    tolerance = float(cfg["touch_tolerance_pct"])
    if tolerance <= 0:
        return 0.0
    return max(0.0, 1.0 - touch.gap_pct / tolerance) * 1.5


def _rejection_bonus(candle: CandleBar, direction: str) -> float:
    """Rewards closing away from the touched boundary - the rejection wick."""
    span = candle.high - candle.low
    if span <= 0:
        return 0.0
    close_position = (candle.close - candle.low) / span
    strength = close_position if direction == "LONG" else 1 - close_position
    return max(0.0, strength - 0.5) * 2
