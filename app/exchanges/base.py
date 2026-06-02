from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ExchangeSymbol:
    exchange: str
    symbol: str
    base: str | None
    quote: str | None
    volume_24h_usd: float
    spread_pct: float | None = None
    depth_1pct_usd: float | None = None


@dataclass(frozen=True)
class TickerEvent:
    exchange: str
    symbol: str
    timestamp: datetime
    price: float
    funding_rate: float | None = None
    open_interest: float | None = None


@dataclass(frozen=True)
class TradeEvent:
    exchange: str
    symbol: str
    timestamp: datetime
    price: float
    qty: float
    side: str

    @property
    def usd(self) -> float:
        return self.price * self.qty


@dataclass(frozen=True)
class OrderbookEvent:
    exchange: str
    symbol: str
    timestamp: datetime
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


class MarketDataCallbacks(Protocol):
    async def on_ticker(self, event: TickerEvent) -> None: ...

    async def on_trade(self, event: TradeEvent) -> None: ...

    async def on_orderbook(self, event: OrderbookEvent) -> None: ...

