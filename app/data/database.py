from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.data.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.is_sqlite = url.startswith("sqlite+aiosqlite:///")
        if url.startswith("sqlite+aiosqlite:///"):
            path = url.removeprefix("sqlite+aiosqlite:///")
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url, future=True)
        if self.is_sqlite:
            self._configure_sqlite_pragmas()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    def _configure_sqlite_pragmas(self) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if self.is_sqlite:
                await self._migrate_sqlite(conn)

    async def _migrate_sqlite(self, conn) -> None:
        existing = {
            row[1] for row in (await conn.execute(text("PRAGMA table_info(signals)"))).fetchall()
        }
        if "manual_entry_price" not in existing:
            await conn.execute(text("ALTER TABLE signals ADD COLUMN manual_entry_price FLOAT"))
        if "manual_entered_at" not in existing:
            await conn.execute(text("ALTER TABLE signals ADD COLUMN manual_entered_at DATETIME"))
        trade_columns = {
            row[1]
            for row in (await conn.execute(text("PRAGMA table_info(paper_trades)"))).fetchall()
        }
        if "profile_id" not in trade_columns:
            await conn.execute(text("ALTER TABLE paper_trades ADD COLUMN profile_id INTEGER"))
        if "profile_key" not in trade_columns:
            await conn.execute(
                text(
                    "ALTER TABLE paper_trades ADD COLUMN profile_key VARCHAR(64) DEFAULT 'default'"
                )
            )
        await conn.execute(
            text("UPDATE paper_trades SET profile_key = 'default' WHERE profile_key IS NULL")
        )

        equity_columns = {
            row[1]
            for row in (
                await conn.execute(text("PRAGMA table_info(paper_equity_curve)"))
            ).fetchall()
        }
        if "profile_id" not in equity_columns:
            await conn.execute(text("ALTER TABLE paper_equity_curve ADD COLUMN profile_id INTEGER"))
        if "profile_key" not in equity_columns:
            await conn.execute(
                text(
                    "ALTER TABLE paper_equity_curve "
                    "ADD COLUMN profile_key VARCHAR(64) DEFAULT 'default'"
                )
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
        ):
            await conn.execute(text(statement))

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
