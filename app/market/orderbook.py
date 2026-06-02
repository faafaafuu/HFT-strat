from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderbookMetrics:
    bid: float
    ask: float
    mid: float
    spread_pct: float
    bid_depth_1pct: float
    ask_depth_1pct: float

    @property
    def depth_1pct_usd(self) -> float:
        return self.bid_depth_1pct + self.ask_depth_1pct


def calculate_orderbook_metrics(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
) -> OrderbookMetrics | None:
    if not bids or not asks:
        return None
    bid = bids[0][0]
    ask = asks[0][0]
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    lower = mid * 0.99
    upper = mid * 1.01
    bid_depth = sum(price * qty for price, qty in bids if lower <= price <= mid)
    ask_depth = sum(price * qty for price, qty in asks if mid <= price <= upper)
    spread_pct = (ask - bid) / mid * 100
    return OrderbookMetrics(
        bid=bid,
        ask=ask,
        mid=mid,
        spread_pct=spread_pct,
        bid_depth_1pct=bid_depth,
        ask_depth_1pct=ask_depth,
    )

