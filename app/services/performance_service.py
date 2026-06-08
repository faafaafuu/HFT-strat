from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings
from app.data.database import Database
from app.services.status_service import RuntimeStatusContext
from app.utils.runtime import active_task_count, memory_usage_mb


class PerformanceService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        runtime: RuntimeStatusContext | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.runtime = runtime or RuntimeStatusContext()

    async def snapshot(self) -> dict[str, Any]:
        return {
            "ram_mb": memory_usage_mb(),
            "active_tasks": active_task_count(),
            "ws_connections": self.runtime.active_websocket_connections,
            "selected_symbols": len(self.runtime.selected_symbols),
            "db_size_mb": self.database.size_mb(),
            "heartbeat_exists": (Path(self.settings.storage.data_dir) / "heartbeat").exists(),
            "signal_cycle_ms": None,
            "orderbook_processing_ms": None,
            "db_write_latency_ms": None,
            "telegram_callback_latency_ms": None,
            "queue_sizes": {},
        }

