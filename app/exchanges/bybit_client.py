from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import aiohttp
import websockets

from app.exchanges.base import (
    ExchangeSymbol,
    MarketDataCallbacks,
    OrderbookEvent,
    TickerEvent,
    TradeEvent,
)
from app.logger import get_logger
from app.utils.math import safe_float
from app.utils.time import ms_to_datetime, utc_now


class BybitClient:
    exchange = "bybit"

    def __init__(
        self,
        testnet: bool = False,
        category: str = "linear",
        ws_topics_per_connection: int = 20,
        orderbook_depth_limit: int = 100,
    ) -> None:
        self.category = category
        self.ws_topics_per_connection = ws_topics_per_connection
        self.orderbook_depth_limit = orderbook_depth_limit
        self.rest_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
        self.ws_url = (
            "wss://stream-testnet.bybit.com/v5/public/linear"
            if testnet
            else "wss://stream.bybit.com/v5/public/linear"
        )
        self.log = get_logger("bybit")
        self.session: aiohttp.ClientSession | None = None
        self._books: dict[str, dict[str, dict[float, float]]] = {}
        self._stop = asyncio.Event()
        self.public_ws_connected = False
        self.public_ws_connections = 0

    async def __aenter__(self) -> BybitClient:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._stop.set()
        if self.session is not None:
            await self.session.close()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        async with self.session.get(f"{self.rest_url}{path}", params=params) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit error {payload.get('retCode')}: {payload.get('retMsg')}")
        return payload.get("result") or {}

    async def instruments_info(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"category": self.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            result = await self._get("/v5/market/instruments-info", params)
            items.extend(result.get("list") or [])
            cursor = result.get("nextPageCursor") or None
            if not cursor:
                break
        return items

    async def tickers(self) -> list[dict[str, Any]]:
        result = await self._get("/v5/market/tickers", {"category": self.category})
        return list(result.get("list") or [])

    async def orderbook(self, symbol: str, limit: int = 200) -> dict[str, Any]:
        return await self._get(
            "/v5/market/orderbook",
            {"category": self.category, "symbol": symbol, "limit": limit},
        )

    async def open_interest(self, symbol: str, interval: str = "5min") -> float | None:
        result = await self._get(
            "/v5/market/open-interest",
            {
                "category": self.category,
                "symbol": symbol,
                "intervalTime": interval,
                "limit": 1,
            },
        )
        rows = result.get("list") or []
        if not rows:
            return None
        return safe_float(rows[0].get("openInterest"), default=0.0)

    async def kline(
        self,
        symbol: str,
        interval: str = "1",
        limit: int = 200,
        start: int | None = None,
        end: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "category": self.category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        result = await self._get(
            "/v5/market/kline",
            params,
        )
        return list(result.get("list") or [])

    async def discover_symbols(self) -> list[ExchangeSymbol]:
        instruments, tickers = await asyncio.gather(self.instruments_info(), self.tickers())
        instrument_by_symbol = {row.get("symbol"): row for row in instruments}
        discovered: list[ExchangeSymbol] = []
        for row in tickers:
            symbol = row.get("symbol")
            info = instrument_by_symbol.get(symbol) or {}
            if info.get("status") not in {None, "Trading"}:
                continue
            last = safe_float(row.get("lastPrice"))
            turnover = safe_float(row.get("turnover24h"))
            bid = safe_float(row.get("bid1Price"))
            ask = safe_float(row.get("ask1Price"))
            spread = (ask - bid) / last * 100 if last and bid and ask and ask >= bid else None
            discovered.append(
                ExchangeSymbol(
                    exchange=self.exchange,
                    symbol=str(symbol),
                    base=info.get("baseCoin"),
                    quote=info.get("quoteCoin"),
                    volume_24h_usd=turnover,
                    spread_pct=spread,
                    depth_1pct_usd=None,
                )
            )
        return discovered

    async def run_public_ws(self, symbols: list[str], callbacks: MarketDataCallbacks) -> None:
        args = self._subscription_args(symbols)
        groups = list(self._chunks(args, self.ws_topics_per_connection))
        if not groups:
            self.log.warning("Bybit public WS skipped: no subscription args")
            return
        tasks = [
            asyncio.create_task(
                self._run_public_ws_group(group_index, group, symbols, callbacks),
                name=f"bybit_ws_{group_index}",
            )
            for group_index, group in enumerate(groups, start=1)
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise
        finally:
            self._stop.set()
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _run_public_ws_group(
        self,
        group_index: int,
        args: list[str],
        symbols: list[str],
        callbacks: MarketDataCallbacks,
    ) -> None:
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=30,
                    ping_timeout=30,
                    max_queue=512,
                ) as ws:
                    self._mark_public_ws_connected(1)
                    self.log.info(
                        "connected Bybit public WS group=%s topics=%s symbols=%s",
                        group_index,
                        len(args),
                        len(symbols),
                    )
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    async for raw in ws:
                        await self._handle_ws_message(raw, callbacks)
                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect loop.
                self.log.warning("Bybit WS disconnected group=%s: %s", group_index, exc)
                await asyncio.sleep(5)
            finally:
                self._mark_public_ws_connected(-1)

    def _mark_public_ws_connected(self, delta: int) -> None:
        self.public_ws_connections = max(0, self.public_ws_connections + delta)
        self.public_ws_connected = self.public_ws_connections > 0

    def _subscription_args(self, symbols: Iterable[str]) -> list[str]:
        args: list[str] = []
        for symbol in symbols:
            args.extend([f"tickers.{symbol}", f"publicTrade.{symbol}", f"orderbook.50.{symbol}"])
        return args

    @staticmethod
    def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
        for idx in range(0, len(values), size):
            yield values[idx : idx + size]

    async def _handle_ws_message(self, raw: str | bytes, callbacks: MarketDataCallbacks) -> None:
        payload = json.loads(raw)
        topic = payload.get("topic", "")
        if not topic or "data" not in payload:
            return
        if topic.startswith("tickers."):
            await self._handle_ticker(payload, callbacks)
        elif topic.startswith("publicTrade."):
            await self._handle_trades(payload, callbacks)
        elif topic.startswith("orderbook."):
            await self._handle_orderbook(payload, callbacks)

    async def _handle_ticker(self, payload: dict[str, Any], callbacks: MarketDataCallbacks) -> None:
        data = payload.get("data") or {}
        symbol = str(data.get("symbol") or payload.get("topic", "").split(".")[-1])
        price = safe_float(data.get("lastPrice") or data.get("markPrice"))
        if price <= 0:
            return
        await callbacks.on_ticker(
            TickerEvent(
                exchange=self.exchange,
                symbol=symbol,
                timestamp=ms_to_datetime(payload.get("ts", int(utc_now().timestamp() * 1000))),
                price=price,
                funding_rate=(
                    safe_float(data.get("fundingRate"), default=0.0)
                    if data.get("fundingRate") is not None
                    else None
                ),
                open_interest=(
                    safe_float(data.get("openInterest"), default=0.0)
                    if data.get("openInterest") is not None
                    else None
                ),
            )
        )

    async def _handle_trades(self, payload: dict[str, Any], callbacks: MarketDataCallbacks) -> None:
        for item in payload.get("data") or []:
            price = safe_float(item.get("p"))
            qty = safe_float(item.get("v"))
            if price <= 0 or qty <= 0:
                continue
            timestamp_value = item.get("T") or payload.get("ts")
            timestamp = (
                ms_to_datetime(timestamp_value)
                if timestamp_value is not None
                else datetime.now(UTC)
            )
            await callbacks.on_trade(
                TradeEvent(
                    exchange=self.exchange,
                    symbol=str(item.get("s")),
                    timestamp=timestamp,
                    price=price,
                    qty=qty,
                    side=str(item.get("S") or "unknown").lower(),
                )
            )

    async def _handle_orderbook(
        self, payload: dict[str, Any], callbacks: MarketDataCallbacks
    ) -> None:
        data = payload.get("data") or {}
        symbol = str(data.get("s") or payload.get("topic", "").split(".")[-1])
        book = self._books.setdefault(symbol, {"b": {}, "a": {}})
        if payload.get("type") == "snapshot":
            book["b"] = {
                price: size
                for price, size in (
                    (safe_float(price), safe_float(size)) for price, size in data.get("b", [])
                )
                if price > 0 and size > 0
            }
            book["a"] = {
                price: size
                for price, size in (
                    (safe_float(price), safe_float(size)) for price, size in data.get("a", [])
                )
                if price > 0 and size > 0
            }
        else:
            for side in ("b", "a"):
                for price_raw, size_raw in data.get(side, []):
                    price = safe_float(price_raw)
                    size = safe_float(size_raw)
                    if price <= 0:
                        continue
                    if size == 0:
                        book[side].pop(price, None)
                    else:
                        book[side][price] = size
        sorted_bids = sorted(book["b"].items(), key=lambda x: x[0], reverse=True)
        sorted_asks = sorted(book["a"].items(), key=lambda x: x[0])
        bids = sorted_bids[:50]
        asks = sorted_asks[:50]
        if not bids or not asks:
            return
        if len(sorted_bids) > self.orderbook_depth_limit:
            book["b"] = dict(sorted_bids[: self.orderbook_depth_limit])
        if len(sorted_asks) > self.orderbook_depth_limit:
            book["a"] = dict(sorted_asks[: self.orderbook_depth_limit])
        await callbacks.on_orderbook(
            OrderbookEvent(
                exchange=self.exchange,
                symbol=symbol,
                timestamp=ms_to_datetime(payload.get("ts", int(utc_now().timestamp() * 1000))),
                bids=bids,
                asks=asks,
            )
        )
