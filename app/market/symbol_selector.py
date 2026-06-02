from __future__ import annotations

import asyncio

from app.config import SymbolsConfig
from app.data.database import Database
from app.data.repositories import MarketRepository
from app.exchanges.base import ExchangeSymbol
from app.exchanges.bybit_client import BybitClient
from app.logger import get_logger
from app.market.orderbook import calculate_orderbook_metrics


class SymbolSelector:
    def __init__(self, config: SymbolsConfig, database: Database) -> None:
        self.config = config
        self.database = database
        self.log = get_logger("symbol_selector")

    async def select_bybit_symbols(self, client: BybitClient) -> list[str]:
        if not self.config.auto_select:
            return self.config.manual_list[: self.config.max_symbols]

        raw_symbols = await client.discover_symbols()
        candidates = [
            item
            for item in raw_symbols
            if item.volume_24h_usd >= self.config.min_24h_volume_usd
            and item.spread_pct is not None
            and item.spread_pct <= self.config.max_spread_pct
        ]
        enriched = await self._enrich_depth(client, candidates[: max(self.config.max_symbols * 3, 50)])
        selected = [
            item
            for item in enriched
            if item.depth_1pct_usd is not None
            and item.depth_1pct_usd >= self.config.min_orderbook_depth_usd_1pct
        ]
        selected.sort(key=lambda x: (x.volume_24h_usd, x.depth_1pct_usd or 0), reverse=True)
        final = selected[: self.config.max_symbols]
        await self._persist(final)
        self.log.info("selected Bybit symbols: %s", ", ".join(item.symbol for item in final))
        return [item.symbol for item in final]

    async def _enrich_depth(
        self,
        client: BybitClient,
        candidates: list[ExchangeSymbol],
    ) -> list[ExchangeSymbol]:
        sem = asyncio.Semaphore(5)

        async def enrich(item: ExchangeSymbol) -> ExchangeSymbol | None:
            async with sem:
                try:
                    raw_book = await client.orderbook(item.symbol, limit=200)
                    bids = [(float(price), float(qty)) for price, qty in raw_book.get("b", [])]
                    asks = [(float(price), float(qty)) for price, qty in raw_book.get("a", [])]
                    metrics = calculate_orderbook_metrics(bids, asks)
                    if metrics is None:
                        return None
                    return ExchangeSymbol(
                        exchange=item.exchange,
                        symbol=item.symbol,
                        base=item.base,
                        quote=item.quote,
                        volume_24h_usd=item.volume_24h_usd,
                        spread_pct=metrics.spread_pct,
                        depth_1pct_usd=metrics.depth_1pct_usd,
                    )
                except Exception as exc:  # noqa: BLE001 - skip weak/unavailable symbol.
                    self.log.debug("depth enrichment failed for %s: %s", item.symbol, exc)
                    return None

        results = await asyncio.gather(*(enrich(item) for item in candidates))
        return [item for item in results if item is not None]

    async def _persist(self, symbols: list[ExchangeSymbol]) -> None:
        async with self.database.session() as session:
            repo = MarketRepository(session)
            for item in symbols:
                await repo.upsert_symbol(
                    exchange=item.exchange,
                    symbol=item.symbol,
                    base=item.base,
                    quote=item.quote,
                    is_active=True,
                    volume_24h_usd=item.volume_24h_usd,
                    spread_pct=item.spread_pct,
                    depth_1pct_usd=item.depth_1pct_usd,
                )

