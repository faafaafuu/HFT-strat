from __future__ import annotations

import asyncio

from app.exchanges.bybit_client import BybitClient
from app.logger import get_logger
from app.market.features import MarketFeatureStore
from app.utils.time import utc_now


class OpenInterestTracker:
    def __init__(
        self,
        bybit: BybitClient,
        feature_store: MarketFeatureStore,
        symbols: list[str],
        interval_seconds: int = 120,
    ) -> None:
        self.bybit = bybit
        self.feature_store = feature_store
        self.symbols = symbols
        self.interval_seconds = interval_seconds
        self.log = get_logger("oi_tracker")
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            for symbol in self.symbols:
                try:
                    oi = await self.bybit.open_interest(symbol)
                    if oi is not None:
                        self.feature_store.on_open_interest("bybit", symbol, utc_now(), oi)
                except Exception as exc:  # noqa: BLE001 - keep polling other symbols.
                    self.log.warning("failed to fetch OI for %s: %s", symbol, exc)
                await asyncio.sleep(0.2)
            await asyncio.sleep(self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()

