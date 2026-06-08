from __future__ import annotations

from typing import Any

from app.data.database import Database
from app.data.models import SignalModel
from app.data.repositories import SignalRepository
from app.services.serializers import normalize_signal_summary


class SignalService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def recent(self, limit: int = 20, offset: int = 0) -> list[SignalModel]:
        async with self.database.session() as session:
            return await SignalRepository(session).list_recent(limit=limit, offset=offset)

    async def detail(self, signal_id: int) -> SignalModel | None:
        async with self.database.session() as session:
            return await SignalRepository(session).get_signal_with_outcomes(signal_id)

    async def summary(self, since=None) -> dict[str, Any]:
        async with self.database.session() as session:
            return normalize_signal_summary(await SignalRepository(session).summary(since=since))
