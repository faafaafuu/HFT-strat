from __future__ import annotations

import asyncio
import contextlib
import signal
from pathlib import Path
from typing import Any

from app.analysis.daily import DailyStrategyAnalysisJob
from app.config import load_settings
from app.data.database import Database
from app.data.repositories import RuntimeSettingsRepository
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
from app.utils.runtime import active_task_count, memory_usage_mb
from app.utils.time import ms_to_datetime, utc_now


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.app.log_level)
    log = get_logger("main")
    database = Database(settings.database.url, backups_dir=settings.storage.backups_dir)
    await database.backup_sqlite("startup")
    await database.init()
    await _apply_runtime_settings(database, settings)
    await database.backup_sqlite("post_start")

    feature_store = MarketFeatureStore(
        retention_minutes=settings.storage.keep_raw_ticks_minutes,
        max_price_points_per_symbol=settings.storage.max_price_points_per_symbol,
        max_trade_points_per_symbol=settings.storage.max_trade_points_per_symbol,
        max_oi_points_per_symbol=settings.storage.max_oi_points_per_symbol,
    )
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
        analysis_job = DailyStrategyAnalysisJob(
            database, interval_hours=settings.storage.strategy_analysis_interval_hours
        )

        tasks = [
            asyncio.create_task(bybit.run_public_ws(symbols, sink), name="bybit_ws"),
            asyncio.create_task(oi_tracker.run(), name="oi_tracker"),
            asyncio.create_task(signal_engine.run(), name="signal_engine"),
            asyncio.create_task(outcome_tracker.run(), name="outcome_tracker"),
            asyncio.create_task(analysis_job.run(), name="strategy_analysis"),
            asyncio.create_task(
                _backup_loop(log, database, settings.storage.backup_interval_hours),
                name="sqlite_backup",
            ),
            asyncio.create_task(
                _retention_loop(log, database, feature_store, settings),
                name="data_retention",
            ),
            asyncio.create_task(
                _heartbeat_loop(log, telegram, bybit, database, feature_store),
                name="heartbeat",
            ),
        ]
        log.info("market heat signal bot started")
        await stop_event.wait()
        log.info("shutdown requested")
        oi_tracker.stop()
        signal_engine.stop()
        outcome_tracker.stop()
        analysis_job.stop()
        feature_store.trim_all()
        await _flush_latest_snapshots(database, feature_store, symbols, settings)
        if paper_manager is not None:
            await paper_manager.persist_state()
        await database.backup_sqlite("shutdown")
        await bybit.close()
        await telegram.stop()

    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await database.close()


async def _apply_runtime_settings(database: Database, settings) -> None:
    async with database.session() as session:
        overrides = await RuntimeSettingsRepository(session).get_all()
    for key, value in overrides.items():
        _set_nested(settings, key, value)


def _set_nested(settings, key: str, value: Any) -> None:
    target = settings
    parts = key.split(".")
    for part in parts[:-1]:
        target = getattr(target, part, None)
        if target is None:
            return
    if hasattr(target, parts[-1]):
        setattr(target, parts[-1], value)


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


async def _heartbeat_loop(
    log,
    telegram: TelegramService,
    bybit: BybitClient,
    database: Database,
    feature_store: MarketFeatureStore,
) -> None:
    heartbeat_path = Path("/app/data/heartbeat")
    while True:
        telegram.last_heartbeat = utc_now()
        telegram.active_websocket_connections = 1 if bybit.public_ws_connected else 0
        try:
            heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            heartbeat_path.write_text(telegram.last_heartbeat.isoformat())
        except OSError as exc:
            log.warning("failed to write heartbeat file: %s", exc)
        memory = memory_usage_mb()
        tasks = active_task_count()
        db_size = database.size_mb()
        counts = feature_store.memory_counts()
        log.info(
            "heartbeat uptime=%s ws_connections=%s symbols=%s memory_mb=%.1f tasks=%s "
            "db_size_mb=%.2f price_points=%s trade_points=%s oi_points=%s orderbooks=%s",
            telegram.last_heartbeat - telegram.started_at,
            telegram.active_websocket_connections,
            len(telegram.symbols),
            memory,
            tasks,
            db_size,
            counts["price_points"],
            counts["trade_points"],
            counts["oi_points"],
            counts["orderbooks"],
        )
        await asyncio.sleep(60)


async def _backup_loop(log, database: Database, interval_hours: int) -> None:
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            path = await database.backup_sqlite("scheduled")
            if path is not None:
                log.info("sqlite backup created path=%s", path)
        except Exception as exc:  # noqa: BLE001 - maintenance loop must keep running.
            log.warning("sqlite backup failed: %s", exc)


async def _retention_loop(
    log, database: Database, feature_store: MarketFeatureStore, settings
) -> None:
    while True:
        try:
            feature_store.trim_all()
            await database.cleanup_retention(
                keep_market_snapshots_days=settings.storage.keep_market_snapshots_days,
                keep_orderbook_events_days=settings.storage.keep_orderbook_events_days,
            )
            log.info("data retention cleanup completed")
        except Exception as exc:  # noqa: BLE001 - maintenance loop must keep running.
            log.warning("data retention cleanup failed: %s", exc)
        await asyncio.sleep(3600)


async def _flush_latest_snapshots(
    database: Database,
    feature_store: MarketFeatureStore,
    symbols: list[str],
    settings,
) -> None:
    async with database.session() as session:
        from app.data.repositories import MarketRepository

        repo = MarketRepository(session)
        for symbol in symbols:
            snapshot = feature_store.snapshot(
                "bybit",
                symbol,
                sweep_lookback_minutes=settings.thresholds.sweep_lookback_minutes,
                sweep_return_minutes=settings.thresholds.sweep_return_minutes,
            )
            if snapshot is None:
                continue
            await repo.add_market_snapshot(
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


if __name__ == "__main__":
    asyncio.run(main())
