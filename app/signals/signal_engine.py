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
        paper_manager: PaperTradeManager | None = None,
        paper_check_interval_seconds: float = 1.0,
    ) -> None:
        self.feature_store = feature_store
        self.paper_manager = paper_manager
        self.paper_check_interval_seconds = paper_check_interval_seconds
        self._last_paper_check: dict[tuple[str, str], float] = {}

    async def on_ticker(self, event: TickerEvent) -> None:
        self.feature_store.on_ticker(event)
        await self._maybe_check_paper(event.exchange, event.symbol, event.price, event.timestamp)

    async def on_trade(self, event: TradeEvent) -> None:
        self.feature_store.on_trade(event)
        await self._maybe_check_paper(event.exchange, event.symbol, event.price, event.timestamp)

    async def on_orderbook(self, event: OrderbookEvent) -> None:
        self.feature_store.on_orderbook(event)
        price = self.feature_store.latest_price(event.exchange, event.symbol)
        if price is not None:
            await self._maybe_check_paper(event.exchange, event.symbol, price, event.timestamp)

    async def _maybe_check_paper(self, exchange: str, symbol: str, price: float, timestamp) -> None:
        if self.paper_manager is None:
            return
        now = asyncio.get_running_loop().time()
        key = (exchange, symbol)
        last = self._last_paper_check.get(key, 0.0)
        if now - last < self.paper_check_interval_seconds:
            return
        self._last_paper_check[key] = now
        await self.paper_manager.on_price(exchange, symbol, price, timestamp)


class SignalEngine:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        feature_store: MarketFeatureStore,
        telegram: TelegramService,
        symbols: list[str],
        paper_manager: PaperTradeManager | None = None,
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
        self.log.info(
            "signal engine started mode=%s paper_manager=%s min_score=%s paper_auto_min_score=%s",
            self.settings.app.mode,
            self.paper_manager is not None,
            self.settings.signals.min_score,
            self.settings.paper.auto_trade_min_score,
        )
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
                        self.log.debug(
                            "signal candidate skipped below min_score exchange=%s symbol=%s "
                            "pattern=%s direction=%s score=%s min_score=%s",
                            candidate.exchange,
                            candidate.symbol,
                            candidate.pattern,
                            candidate.direction,
                            score,
                            self.settings.signals.min_score,
                        )
                        continue
                    since = utc_now() - timedelta(
                        minutes=self.settings.signals.cooldown_minutes_per_symbol
                    )
                    existing = await signal_repo.latest_signal_for_symbol(
                        candidate.exchange,
                        candidate.symbol,
                        since=since,
                    )
                    if existing is not None:
                        self.log.debug(
                            "signal candidate skipped by cooldown exchange=%s symbol=%s "
                            "pattern=%s direction=%s score=%s cooldown_minutes=%s",
                            candidate.exchange,
                            candidate.symbol,
                            candidate.pattern,
                            candidate.direction,
                            score,
                            self.settings.signals.cooldown_minutes_per_symbol,
                        )
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
                    paper_enabled = (
                        self.paper_manager is not None
                        and self.settings.app.mode == "paper_trading"
                        and self.settings.paper.enabled
                    )
                    self.log.info(
                        "signal created signal_id=%s exchange=%s symbol=%s direction=%s "
                        "pattern=%s score=%s paper_enabled=%s paper_profiles=%s",
                        signal.id,
                        signal.exchange,
                        signal.symbol,
                        signal.direction,
                        signal.pattern,
                        signal.score,
                        paper_enabled,
                        ",".join(self.settings.paper.profiles.keys()),
                    )
                    if paper_enabled:
                        paper_candidates.append(signal)
                    elif self.settings.app.mode != "paper_trading":
                        self.log.debug(
                            "paper auto-open skipped signal_id=%s reason=app_mode mode=%s",
                            signal.id,
                            self.settings.app.mode,
                        )
                    elif self.paper_manager is None:
                        self.log.warning(
                            "paper auto-open skipped signal_id=%s reason=paper_manager_missing",
                            signal.id,
                        )
                    elif not self.settings.paper.enabled:
                        self.log.debug(
                            "paper auto-open skipped signal_id=%s reason=paper_disabled",
                            signal.id,
                        )
        for signal in paper_candidates:
            self.log.info(
                "paper auto-open requested signal_id=%s exchange=%s symbol=%s direction=%s "
                "score=%s profiles=%s",
                signal.id,
                signal.exchange,
                signal.symbol,
                signal.direction,
                signal.score,
                ",".join(self.settings.paper.profiles.keys()),
            )
            trades = await self.paper_manager.open_for_signal(signal)
            if not trades:
                self.log.warning(
                    "paper auto-open rejected signal_id=%s exchange=%s symbol=%s",
                    signal.id,
                    signal.exchange,
                    signal.symbol,
                )
            for trade in trades:
                self.log.info(
                    "paper trade opened trade_id=%s signal_id=%s exchange=%s symbol=%s "
                    "profile=%s direction=%s entry=%s stop=%s take=%s position_usd=%s risk_usd=%s",
                    trade.id,
                    signal.id,
                    trade.exchange,
                    trade.symbol,
                    trade.profile_key,
                    trade.direction,
                    trade.entry_price,
                    trade.stop_price,
                    trade.take_price,
                    trade.position_size_usd,
                    trade.risk_usd,
                )
