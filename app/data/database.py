from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.data.models import Base


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite+aiosqlite:///"):
            path = url.removeprefix("sqlite+aiosqlite:///")
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url, future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if self.engine.url.get_backend_name().startswith("sqlite"):
                await conn.execute(text("PRAGMA table_info(signals)"))
                existing = {
                    row[1]
                    for row in (await conn.execute(text("PRAGMA table_info(signals)"))).fetchall()
                }
                if "manual_entry_price" not in existing:
                    await conn.execute(text("ALTER TABLE signals ADD COLUMN manual_entry_price FLOAT"))
                if "manual_entered_at" not in existing:
                    await conn.execute(text("ALTER TABLE signals ADD COLUMN manual_entered_at DATETIME"))

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
