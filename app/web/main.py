from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings, load_settings
from app.data.database import Database
from app.services.analytics_service import AnalyticsService
from app.services.paper_service import PaperService
from app.services.performance_service import PerformanceService
from app.services.signal_service import SignalService
from app.services.status_service import StatusService
from app.services.strategy_lab_service import StrategyLabService
from app.web.api import router as api_router
from app.web.routes import router as page_router


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    init_database: bool = True,
    service_overrides: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database_local = database or Database(
            settings.database.url, backups_dir=settings.storage.backups_dir
        )
        if init_database and not service_overrides:
            await database_local.init()
        app.state.settings = settings
        app.state.database = database_local
        service_overrides_local = service_overrides or {}
        app.state.status_service = service_overrides_local.get(
            "status_service", StatusService(database_local, settings)
        )
        app.state.signal_service = service_overrides_local.get(
            "signal_service", SignalService(database_local)
        )
        app.state.paper_service = service_overrides_local.get(
            "paper_service", PaperService(database_local)
        )
        app.state.analytics_service = service_overrides_local.get(
            "analytics_service", AnalyticsService(database_local)
        )
        app.state.performance_service = service_overrides_local.get(
            "performance_service", PerformanceService(database_local, settings)
        )
        app.state.strategy_lab_service = service_overrides_local.get(
            "strategy_lab_service", StrategyLabService(database_local, settings)
        )
        yield
        if not service_overrides:
            await database_local.close()

    app = FastAPI(title="Market Heat Signal Bot", lifespan=lifespan)
    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    templates.env.filters["money"] = _money
    templates.env.filters["pct"] = _pct
    templates.env.filters["num"] = _num
    templates.env.filters["time"] = _time
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health")
    async def api_health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(page_router)
    app.include_router(api_router)
    return app


def _money(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    sign = "+" if number > 0 else ""
    return f"{sign}${number:,.2f}"


def _pct(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _num(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.2f}"


def _time(value: object) -> str:
    if value is None:
        return "n/a"
    return str(value)


def main() -> None:
    uvicorn.run(
        "app.web.main:app",
        host=os.getenv("WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("WEB_PORT", "8080")),
        log_level="info",
    )


app = create_app()


if __name__ == "__main__":
    main()
