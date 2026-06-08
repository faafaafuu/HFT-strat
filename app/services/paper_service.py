from __future__ import annotations

from typing import Any

from app.data.database import Database
from app.data.models import PaperTradeModel
from app.paper.statistics import (
    paper_profile_summary,
    paper_profile_trades,
    paper_profiles_summary,
    paper_summary,
)


class PaperService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def portfolio(self) -> dict[str, Any]:
        async with self.database.session() as session:
            return await paper_summary(session)

    async def profiles(self) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            return await paper_profiles_summary(session)

    async def profile(self, profile_key: str) -> dict[str, Any]:
        async with self.database.session() as session:
            return await paper_profile_summary(session, profile_key)

    async def trades(
        self,
        profile_key: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[PaperTradeModel]:
        async with self.database.session() as session:
            if profile_key is not None:
                return await paper_profile_trades(
                    session, profile_key, status=status, limit=limit
                )
            from sqlalchemy import select

            filters = []
            if status == "CLOSED":
                filters.append(PaperTradeModel.status != "OPEN")
            elif status is not None:
                filters.append(PaperTradeModel.status == status)
            return list(
                (
                    await session.scalars(
                        select(PaperTradeModel)
                        .where(*filters)
                        .order_by(PaperTradeModel.opened_at.desc())
                        .limit(limit)
                    )
                ).all()
            )

