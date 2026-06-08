from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.config import Settings
from app.data.database import Database
from app.data.models import PaperTradeModel, SymbolModel
from app.data.repositories import SignalRepository, _aware
from app.services.serializers import normalize_signal_summary
from app.utils.runtime import active_task_count, memory_usage_mb
from app.utils.time import utc_now


@dataclass(slots=True)
class RuntimeStatusContext:
    started_at: datetime | None = None
    online: bool = True
    selected_symbols: list[str] = field(default_factory=list)
    last_heartbeat: datetime | None = None
    active_websocket_connections: int = 0


class StatusService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        runtime: RuntimeStatusContext | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.runtime = runtime or RuntimeStatusContext()

    async def dashboard(self) -> dict[str, Any]:
        now = utc_now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = today - timedelta(days=today.weekday())
        async with self.database.session() as session:
            signal_repo = SignalRepository(session)
            today_count = await signal_repo.count_since(today)
            week_count = await signal_repo.count_since(week)
            summary = normalize_signal_summary(await signal_repo.summary())
            last_signal_time = await signal_repo.last_signal_time()
            open_paper_trades = int(
                await session.scalar(
                    select(func.count(PaperTradeModel.id)).where(PaperTradeModel.status == "OPEN")
                )
                or 0
            )
            selected_symbols = self.runtime.selected_symbols or list(
                (
                    await session.scalars(
                        select(SymbolModel.symbol)
                        .where(SymbolModel.is_active.is_(True))
                        .order_by(SymbolModel.volume_24h_usd.desc().nullslast())
                        .limit(self.settings.symbols.max_symbols)
                    )
                ).all()
            )
        heartbeat = self.runtime.last_heartbeat or self._heartbeat_from_disk()
        started_at = self.runtime.started_at or heartbeat or now
        return {
            "online": self.runtime.online,
            "pairs_count": len(selected_symbols),
            "signals_today": today_count,
            "signals_week": week_count,
            "best_pattern": summary.get("best_pattern"),
            "best_pair": summary.get("best_pair"),
            "uptime": now - _aware(started_at),
            "last_heartbeat": heartbeat,
            "active_websocket_connections": self.runtime.active_websocket_connections,
            "selected_symbols": selected_symbols,
            "last_signal_time": last_signal_time,
            "memory_mb": memory_usage_mb(),
            "active_tasks": active_task_count(),
            "db_size_mb": self.database.size_mb(),
            "open_paper_trades": open_paper_trades,
        }

    def _heartbeat_from_disk(self) -> datetime | None:
        path = Path(self.settings.storage.data_dir) / "heartbeat"
        if not path.exists():
            return None
        try:
            value = datetime.fromisoformat(path.read_text().strip())
        except (OSError, ValueError):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
