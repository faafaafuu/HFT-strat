from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.data.models import Base


class Database:
    def __init__(self, url: str, backups_dir: str | Path = "/app/backups") -> None:
        self.url = url
        self.backups_dir = Path(backups_dir)
        self.is_sqlite = url.startswith("sqlite+aiosqlite:///")
        self.sqlite_path: Path | None = None
        if url.startswith("sqlite+aiosqlite:///"):
            path = url.removeprefix("sqlite+aiosqlite:///")
            if path and path != ":memory:":
                self.sqlite_path = Path(path)
                self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url, future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        await self.backup_sqlite("pre_migration")
        async with self.engine.begin() as conn:
            if self.is_sqlite:
                await conn.execute(text("PRAGMA foreign_keys=ON"))
                await conn.execute(text("PRAGMA busy_timeout=5000"))
                await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.run_sync(Base.metadata.create_all)
            if self.is_sqlite:
                await self._migrate_sqlite(conn)

    async def _migrate_sqlite(self, conn) -> None:
        existing = {
            row[1] for row in (await conn.execute(text("PRAGMA table_info(signals)"))).fetchall()
        }
        if "manual_entry_price" not in existing:
            await _add_column_if_missing(
                conn, "signals", "manual_entry_price", "ALTER TABLE signals ADD COLUMN manual_entry_price FLOAT"
            )
        if "manual_entered_at" not in existing:
            await _add_column_if_missing(
                conn, "signals", "manual_entered_at", "ALTER TABLE signals ADD COLUMN manual_entered_at DATETIME"
            )
        for column, ddl in {
            "strategy_key": "ALTER TABLE signals ADD COLUMN strategy_key VARCHAR(64)",
            "strategy_instance_id": (
                "ALTER TABLE signals ADD COLUMN strategy_instance_id VARCHAR(64)"
            ),
            "strategy_profile_key": (
                "ALTER TABLE signals ADD COLUMN strategy_profile_key VARCHAR(64)"
            ),
            "paper_profile_key": "ALTER TABLE signals ADD COLUMN paper_profile_key VARCHAR(64)",
            "invalidation_level": "ALTER TABLE signals ADD COLUMN invalidation_level FLOAT",
            "suggested_stop_pct": "ALTER TABLE signals ADD COLUMN suggested_stop_pct FLOAT",
            "suggested_take_pct": "ALTER TABLE signals ADD COLUMN suggested_take_pct FLOAT",
            "confidence": "ALTER TABLE signals ADD COLUMN confidence FLOAT",
            "ml_signal_quality_score": (
                "ALTER TABLE signals ADD COLUMN ml_signal_quality_score FLOAT"
            ),
        }.items():
            if column not in existing:
                await _add_column_if_missing(conn, "signals", column, ddl)
        trade_columns = {
            row[1]
            for row in (await conn.execute(text("PRAGMA table_info(paper_trades)"))).fetchall()
        }
        if "profile_id" not in trade_columns:
            await _add_column_if_missing(
                conn, "paper_trades", "profile_id", "ALTER TABLE paper_trades ADD COLUMN profile_id INTEGER"
            )
        if "profile_key" not in trade_columns:
            await _add_column_if_missing(
                conn,
                "paper_trades",
                "profile_key",
                "ALTER TABLE paper_trades ADD COLUMN profile_key VARCHAR(64) DEFAULT 'default'",
            )
        await conn.execute(
            text("UPDATE paper_trades SET profile_key = 'default' WHERE profile_key IS NULL")
        )
        for column, ddl in {
            "strategy_key": "ALTER TABLE paper_trades ADD COLUMN strategy_key VARCHAR(64)",
            "strategy_instance_id": (
                "ALTER TABLE paper_trades ADD COLUMN strategy_instance_id VARCHAR(64)"
            ),
            "strategy_profile_key": (
                "ALTER TABLE paper_trades ADD COLUMN strategy_profile_key VARCHAR(64)"
            ),
        }.items():
            if column not in trade_columns:
                await _add_column_if_missing(conn, "paper_trades", column, ddl)

        equity_columns = {
            row[1]
            for row in (
                await conn.execute(text("PRAGMA table_info(paper_equity_curve)"))
            ).fetchall()
        }
        if "profile_id" not in equity_columns:
            await _add_column_if_missing(
                conn,
                "paper_equity_curve",
                "profile_id",
                "ALTER TABLE paper_equity_curve ADD COLUMN profile_id INTEGER",
            )
        if "profile_key" not in equity_columns:
            await _add_column_if_missing(
                conn,
                "paper_equity_curve",
                "profile_key",
                "ALTER TABLE paper_equity_curve ADD COLUMN profile_key VARCHAR(64) DEFAULT 'default'",
            )
        await conn.execute(
            text(
                "UPDATE paper_equity_curve SET profile_key = 'default' " "WHERE profile_key IS NULL"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_paper_trade_signal_profile "
                "ON paper_trades(signal_id, profile_key) WHERE signal_id IS NOT NULL"
            )
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS ix_paper_trades_profile_key ON paper_trades(profile_key)",
            "CREATE INDEX IF NOT EXISTS ix_paper_trades_symbol ON paper_trades(symbol)",
            "CREATE INDEX IF NOT EXISTS ix_paper_trades_status ON paper_trades(status)",
            "CREATE INDEX IF NOT EXISTS ix_paper_trades_signal_id ON paper_trades(signal_id)",
            "CREATE INDEX IF NOT EXISTS ix_paper_trades_opened_at ON paper_trades(opened_at)",
            "CREATE INDEX IF NOT EXISTS ix_paper_equity_curve_profile_key "
            "ON paper_equity_curve(profile_key)",
            "CREATE INDEX IF NOT EXISTS ix_market_snapshots_timestamp "
            "ON market_snapshots(timestamp)",
            "CREATE INDEX IF NOT EXISTS ix_orderbook_events_timestamp "
            "ON orderbook_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS ix_strategy_analysis_period_profile "
            "ON strategy_analysis(period_start, period_end, profile_key)",
            "CREATE INDEX IF NOT EXISTS ix_signals_strategy_key ON signals(strategy_key)",
            "CREATE INDEX IF NOT EXISTS ix_signals_strategy_instance_id "
            "ON signals(strategy_instance_id)",
            "CREATE INDEX IF NOT EXISTS ix_signals_strategy_profile_key "
            "ON signals(strategy_profile_key)",
            "CREATE INDEX IF NOT EXISTS ix_signals_paper_profile_key ON signals(paper_profile_key)",
            "CREATE INDEX IF NOT EXISTS ix_paper_trades_strategy_key ON paper_trades(strategy_key)",
            "CREATE INDEX IF NOT EXISTS ix_paper_trades_strategy_instance_id "
            "ON paper_trades(strategy_instance_id)",
            "CREATE INDEX IF NOT EXISTS ix_paper_trades_strategy_profile_key "
            "ON paper_trades(strategy_profile_key)",
            "CREATE INDEX IF NOT EXISTS ix_historical_candles_lookup "
            "ON historical_candles(exchange, symbol, timeframe, open_time)",
            "CREATE INDEX IF NOT EXISTS ix_backtest_runs_strategy_symbol "
            "ON backtest_runs(strategy_key, symbol, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_backtest_trades_run_id ON backtest_trades(run_id)",
            "CREATE INDEX IF NOT EXISTS ix_jobs_status_type ON jobs(status, job_type)",
            "CREATE INDEX IF NOT EXISTS ix_density_events_symbol_timestamp "
            "ON density_events(symbol, timestamp)",
            "CREATE INDEX IF NOT EXISTS ix_density_events_side_event_type "
            "ON density_events(side, event_type)",
            "CREATE INDEX IF NOT EXISTS ix_density_levels_symbol_status "
            "ON density_levels(symbol, status)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_density_level_identity "
            "ON density_levels(exchange, symbol, side, price)",
            "CREATE INDEX IF NOT EXISTS ix_ml_model_runs_active ON ml_model_runs(is_active)",
        ):
            await conn.execute(text(statement))

    async def cleanup_retention(
        self,
        keep_market_snapshots_days: int,
        keep_orderbook_events_days: int,
    ) -> None:
        market_cutoff = datetime.utcnow() - timedelta(days=keep_market_snapshots_days)
        orderbook_cutoff = datetime.utcnow() - timedelta(days=keep_orderbook_events_days)
        async with self.session() as session:
            await session.execute(
                text("DELETE FROM market_snapshots WHERE timestamp < :cutoff"),
                {"cutoff": market_cutoff},
            )
            await session.execute(
                text("DELETE FROM orderbook_events WHERE timestamp < :cutoff"),
                {"cutoff": orderbook_cutoff},
            )

    async def backup_sqlite(self, reason: str = "manual") -> Path | None:
        if self.sqlite_path is None or str(self.sqlite_path) == ":memory:":
            return None
        if not self.sqlite_path.exists():
            return None
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
        backup_path = self.backups_dir / f"bot_{timestamp}.sqlite3"
        counter = 1
        while backup_path.exists():
            backup_path = self.backups_dir / f"bot_{timestamp}_{counter}.sqlite3"
            counter += 1
        source = sqlite3.connect(str(self.sqlite_path))
        try:
            destination = sqlite3.connect(str(backup_path))
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        return backup_path

    def size_mb(self) -> float:
        if self.sqlite_path is None:
            return 0.0
        paths = [
            self.sqlite_path,
            Path(f"{self.sqlite_path}-wal"),
            Path(f"{self.sqlite_path}-shm"),
        ]
        size = sum(path.stat().st_size for path in paths if path.exists())
        return size / 1024 / 1024

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


async def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
    if column in {row[1] for row in rows}:
        return
    try:
        await conn.execute(text(ddl))
    except OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise
