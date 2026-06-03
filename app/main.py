from __future__ import annotations

import asyncio
import contextlib
import signal
from pathlib import Path

from app.config import load_settings
from app.data.database import Database
from app.exchanges.base import TickerEvent
from app.exchanges.bybit_client import BybitClient
from app.logger import get_logger, setup_logging
from app.market.features import MarketFeatureStore
from app.market.oi_tracker import OpenInterestTracker
from app.market.symbol_selector import SymbolSelector
from app.paper.manager import PaperTradeManager
from app.signals.outcome_tracker import OutcomeTracker
from app.signals.signal_engine import MarketEventSink, SignalEngine
from app.telegram.bot import TelegramService
from app.utils.math import safe_float
from app.utils.time import ms_to_datetime, utc_now


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.app.log_level)
    log = get_logger("main")
    database = Database(settings.database.url)
    await database.init()

    feature_store = MarketFeatureStore()
    telegram = TelegramService(settings, database, feature_store=feature_store)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    tasks: list[asyncio.Task[None]] = []
    async with BybitClient(
        testnet=settings.exchanges.bybit.testnet,
        category=settings.exchanges.bybit.market_type,
    ) as bybit:
        selector = SymbolSelector(settings.symbols, database)
        try:
            symbols = await selector.select_bybit_symbols(bybit)
        except Exception as exc:  # noqa: BLE001 - allow manual fallback.
            log.warning("auto symbol selection failed, using manual list: %s", exc)
            symbols = settings.symbols.manual_list[: settings.symbols.max_symbols]
        if not symbols:
            symbols = settings.symbols.manual_list[: settings.symbols.max_symbols]
        telegram.symbols = symbols

        await _seed_bybit_history(bybit, feature_store, symbols)
        await telegram.start()

        paper_manager = None
        if settings.app.mode == "paper_trading":
            paper_manager = PaperTradeManager(database, settings.paper, notifier=telegram)
            await paper_manager.ensure_account()
            log.info(
                "paper trading enabled auto_trade_min_score=%s max_open_positions=%s "
                "risk_per_trade_pct=%s leverage=%s",
                settings.paper.auto_trade_min_score,
                settings.paper.max_open_positions,
                settings.paper.risk_per_trade_pct,
                settings.paper.leverage,
            )
        else:
            log.info("paper trading disabled app_mode=%s", settings.app.mode)

        sink = MarketEventSink(feature_store, paper_manager=paper_manager)
        oi_tracker = OpenInterestTracker(bybit, feature_store, symbols)
        signal_engine = SignalEngine(
            settings,
            database,
            feature_store,
            telegram,
            symbols,
            paper_manager=paper_manager,
        )
        outcome_tracker = OutcomeTracker(database, feature_store, settings.outcomes)

        tasks = [
            asyncio.create_task(bybit.run_public_ws(symbols, sink), name="bybit_ws"),
            asyncio.create_task(oi_tracker.run(), name="oi_tracker"),
            asyncio.create_task(signal_engine.run(), name="signal_engine"),
            asyncio.create_task(outcome_tracker.run(), name="outcome_tracker"),
            asyncio.create_task(_heartbeat_loop(log, telegram, bybit), name="heartbeat"),
        ]
        log.info("market heat signal bot started")
        await stop_event.wait()
        log.info("shutdown requested")
        oi_tracker.stop()
        signal_engine.stop()
        outcome_tracker.stop()
        await bybit.close()
        await telegram.stop()

    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await database.close()


async def _seed_bybit_history(
    bybit: BybitClient,
    feature_store: MarketFeatureStore,
    symbols: list[str],
) -> None:
    log = get_logger("seed")
    ticker_by_symbol = {}
    try:
        ticker_by_symbol = {item.get("symbol"): item for item in await bybit.tickers()}
    except Exception as exc:  # noqa: BLE001 - kline warmup can still proceed.
        log.warning("failed to fetch seed tickers: %s", exc)
    for symbol in symbols:
        try:
            rows = await bybit.kline(symbol, interval="1", limit=200)
            for row in sorted(rows, key=lambda item: int(item[0])):
                ts = ms_to_datetime(int(row[0]))
                close = safe_float(row[4])
                turnover = safe_float(row[6])
                if close > 0:
                    feature_store.seed_candle("bybit", symbol, ts, close, turnover)
            ticker = ticker_by_symbol.get(symbol)
            if ticker:
                price = safe_float(ticker.get("lastPrice") or ticker.get("markPrice"))
                if price > 0:
                    feature_store.on_ticker(
                        TickerEvent(
                            exchange="bybit",
                            symbol=symbol,
                            timestamp=utc_now(),
                            price=price,
                            funding_rate=(
                                safe_float(ticker.get("fundingRate"), default=0.0)
                                if ticker.get("fundingRate") is not None
                                else None
                            ),
                            open_interest=(
                                safe_float(ticker.get("openInterest"), default=0.0)
                                if ticker.get("openInterest") is not None
                                else None
                            ),
                        )
                    )
        except Exception as exc:  # noqa: BLE001 - one symbol should not block startup.
            log.warning("failed to seed %s: %s", symbol, exc)


async def _heartbeat_loop(log, telegram: TelegramService, bybit: BybitClient) -> None:
    heartbeat_path = Path("storage/heartbeat")
    while True:
        telegram.last_heartbeat = utc_now()
        telegram.active_websocket_connections = 1 if bybit.public_ws_connected else 0
        try:
            heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            heartbeat_path.write_text(telegram.last_heartbeat.isoformat())
        except OSError as exc:
            log.warning("failed to write heartbeat file: %s", exc)
        log.info(
            "heartbeat uptime=%s ws_connections=%s symbols=%s",
            telegram.last_heartbeat - telegram.started_at,
            telegram.active_websocket_connections,
            len(telegram.symbols),
        )
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
