from datetime import UTC, datetime

import pytest

from app.config import PaperConfig
from app.exchanges.base import OrderbookEvent
from app.paper.manager import PaperTradeManager
from app.signals.signal_engine import MarketEventSink


class _FeatureStore:
    def __init__(self) -> None:
        self.orderbook_calls = 0

    def on_orderbook(self, event: OrderbookEvent) -> None:
        self.orderbook_calls += 1

    def latest_price(self, exchange: str, symbol: str) -> float:
        return 100.0


@pytest.mark.asyncio
async def test_orderbook_events_are_throttled_per_symbol() -> None:
    store = _FeatureStore()
    sink = MarketEventSink(store, orderbook_process_interval_seconds=60)
    event = OrderbookEvent(
        exchange="bybit",
        symbol="BTCUSDT",
        timestamp=datetime.now(UTC),
        bids=[(99.9, 1.0)],
        asks=[(100.1, 1.0)],
    )

    await sink.on_orderbook(event)
    await sink.on_orderbook(event)

    assert store.orderbook_calls == 1


@pytest.mark.asyncio
async def test_paper_price_update_skips_database_without_open_symbol() -> None:
    class _Database:
        def session(self):  # pragma: no cover - should not be called.
            raise AssertionError("database should not be touched without open symbol")

    manager = PaperTradeManager(database=_Database(), config=PaperConfig())

    await manager.on_price("bybit", "BTCUSDT", 100.0, datetime.now(UTC))

    assert manager.latest_prices[("bybit", "BTCUSDT")] == 100.0
