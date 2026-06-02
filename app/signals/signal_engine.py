from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from app.config import Settings
from app.data.database import Database
from app.data.repositories import MarketRepository, SignalRepository
from app.exchanges.base import MarketDataCallbacks, OrderbookEvent, TickerEvent, TradeEvent
from app.logger import get_logger
from app.market.features import MarketFeatureStore
from app.signals.patterns import detect_patterns
from app.signals.scoring import score_signal
from app.telegram.bot import TelegramService
from app.utils.time import utc_now

if TYPE_CHECKING:
    from app.paper.manager import PaperTradeManager


class MarketEventSink(MarketDataCallbacks):
    def __init__(
        self,
        feature_store: MarketFeatureStore,
        paper_manager: "PaperTradeManager | None" = None,
    ) -> None:
        self.feature_store = feature_store
        self.paper_manager = paper_manager

    async def on_ticker(self, event: TickerEvent) -> None:
        self.feature_store.on_ticker(event)
        if self.paper_manager is not None:
            await self.paper_manager.on_price(event.exchange, event.symbol, event.price, event.timestamp)

    async def on_trade(self, event: TradeEvent) -> None:
        self.feature_store.on_trade(event)
        if self.paper_manager is not None:
            await self.paper_manager.on_price(event.exchange, event.symbol, event.price, event.timestamp)

    async def on_orderbook(self, event: OrderbookEvent) -> None:
        self.feature_store.on_orderbook(event)


class SignalEngine:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        feature_store: MarketFeatureStore,
        telegram: TelegramService,
        symbols: list[str],
        paper_manager: "PaperTradeManager | None" = None,
        exchange: str = "bybit",
        interval_seconds: int = 15,
    ) -> None:
        self.settings = settings
        self.database = database
        self.feature_store = feature_store
        self.telegram = telegram
        self.symbols = symbols
        self.paper_manager = paper_manager
        self.exchange = exchange
        self.interval_seconds = interval_seconds
        self.log = get_logger("signal_engine")
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.evaluate_once()
            except Exception as exc:  # noqa: BLE001 - engine must keep running.
                self.log.warning("signal evaluation failed: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()

    async def evaluate_once(self) -> None:
        paper_candidates = []
        async with self.database.session() as session:
            market_repo = MarketRepository(session)
            signal_repo = SignalRepository(session)
            for symbol in self.symbols:
                snapshot = self.feature_store.snapshot(
                    self.exchange,
                    symbol,
                    sweep_lookback_minutes=self.settings.thresholds.sweep_lookback_minutes,
                    sweep_return_minutes=self.settings.thresholds.sweep_return_minutes,
                )
                if snapshot is None:
                    continue
                await market_repo.add_market_snapshot(
                    exchange=snapshot.exchange,
                    symbol=snapshot.symbol,
                    timestamp=snapshot.timestamp,
                    price=snapshot.price,
                    volume_1m=snapshot.volume_1m_usd,
                    volume_5m=snapshot.volume_5m_usd,
                    oi=snapshot.oi,
                    oi_change_5m=snapshot.oi_change_5m_pct,
                    oi_change_15m=snapshot.oi_change_15m_pct,
                    funding_rate=snapshot.funding_rate_pct,
                    spread_pct=snapshot.spread_pct,
                    bid_depth_1pct=snapshot.bid_depth_1pct,
                    ask_depth_1pct=snapshot.ask_depth_1pct,
                )
                if self.telegram.paused:
                    continue
                for candidate in detect_patterns(snapshot, self.settings.thresholds):
                    score = score_signal(candidate, self.settings.thresholds)
                    if score < self.settings.signals.min_score:
                        continue
                    since = utc_now() - timedelta(minutes=self.settings.signals.cooldown_minutes_per_symbol)
                    existing = await signal_repo.latest_signal_for_symbol(
                        candidate.exchange,
                        candidate.symbol,
                        since=since,
                    )
                    if existing is not None:
                        continue
                    signal = await signal_repo.add_signal(
                        exchange=candidate.exchange,
                        symbol=candidate.symbol,
                        timestamp=utc_now(),
                        direction=candidate.direction,
                        pattern=candidate.pattern,
                        score=score,
                        entry_price=candidate.entry_price,
                        reasons=candidate.reasons,
                        market_context=candidate.context,
                    )
                    await self.telegram.send_signal(signal, candidate.reasons, candidate.context)
                    if (
                        self.paper_manager is not None
                        and self.settings.app.mode == "paper_trading"
                        and score >= self.settings.paper.auto_trade_min_score
                    ):
                        paper_candidates.append(signal)
                    self.log.info(
                        "signal %s %s %s score=%s",
                        signal.exchange,
                        signal.symbol,
                        signal.direction,
                        signal.score,
                    )
        for signal in paper_candidates:
            await self.paper_manager.open_from_signal(signal)
