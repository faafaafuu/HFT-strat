from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings, load_settings
from app.data.database import Database
from app.logger import get_logger
from app.runtime_settings import apply_runtime_settings, refresh_runtime_settings_loop
from app.services.analytics_service import AnalyticsService
from app.services.chart_service import ChartService
from app.services.paper_service import PaperService
from app.services.performance_service import PerformanceService
from app.services.signal_service import SignalService
from app.services.status_service import StatusService
from app.services.strategy_lab_service import StrategyLabService
from app.web.actions import router as actions_router
from app.web.api import router as api_router
from app.web.auth import (
    _RedirectToLogin,
    install_session_middleware,
    redirect_auth_exception_handler,
)
from app.web.labels import format_value, label_for
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
        refresh_task: asyncio.Task[None] | None = None
        if not service_overrides:
            # Without this the dashboard would render config.yaml defaults and hide
            # every override the bot is actually running with.
            await apply_runtime_settings(database_local, settings)
            refresh_task = asyncio.create_task(
                refresh_runtime_settings_loop(
                    get_logger("web_runtime_settings"), database_local, settings
                )
            )
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
        app.state.chart_service = service_overrides_local.get(
            "chart_service", ChartService(database_local)
        )
        yield
        if refresh_task is not None:
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task
        if not service_overrides:
            await database_local.close()

    app = FastAPI(title="Market Heat Signal Bot", lifespan=lifespan)
    # Templates read settings directly, so it must exist even when lifespan never runs (tests).
    app.state.settings = settings
    install_session_middleware(app, settings)
    app.add_exception_handler(_RedirectToLogin, redirect_auth_exception_handler)
    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    templates.env.filters["money"] = _money
    templates.env.filters["usd"] = _usd
    templates.env.filters["pct"] = _pct
    templates.env.filters["num"] = _num
    templates.env.filters["time"] = _time
    templates.env.filters["duration"] = _duration
    templates.env.filters["ru_status"] = _ru_status
    templates.env.filters["ru_direction"] = _ru_direction
    templates.env.filters["ru_job"] = _ru_job
    templates.env.filters["ru_event"] = _ru_event
    templates.env.filters["ago"] = _ago
    templates.env.filters["outcome"] = _outcome
    templates.env.filters["fromjson"] = _fromjson
    templates.env.filters["param_label"] = label_for
    templates.env.filters["param_value"] = lambda value, key: format_value(key, value)
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
    app.include_router(actions_router)
    return app


def _money(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    sign = "+" if number > 0 else "-" if number < 0 else ""
    return f"{sign}${abs(number):,.2f}"


def _usd(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"${number:,.2f}"


def _pct(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:.2f}%"


def _num(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.2f}"


def _time(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


def _duration(value: object) -> str:
    if not isinstance(value, timedelta):
        return str(value) if value is not None else "n/a"
    total = int(value.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


_RU_STATUS = {
    "OPEN": "Открыта",
    "CLOSED": "Закрыта",
    "CLOSED_TP": "Тейк",
    "CLOSED_SL": "Стоп",
    "EXPIRED": "Таймаут",
    "PENDING": "В очереди",
    "ACTIVE": "Активен",
    "NEW": "Новый",
    "IGNORED": "Пропущен",
    "ENTERED": "В позиции",
    "queued": "В очереди",
    "running": "Выполняется",
    "done": "Готово",
    "success": "Готово",
    "failed": "Ошибка",
    "error": "Ошибка",
    "cancelling": "Останавливается",
    "cancelled": "Отменена",
}

_RU_JOB = {
    "download_history": "Загрузка истории",
    "run_backtest": "Бэктест",
    "run_hyperopt": "Гипероптимизация",
    "train_ml_model": "Обучение ML",
    "run_density_analysis": "Анализ плотностей",
}

_RU_EVENT = {
    "absorbed": "Поглощение",
    "absorption": "Поглощение",
    "eaten": "Съедена",
    "spoof": "Спуфинг",
    "spoofed": "Спуфинг",
    "pulled": "Снята",
    "appeared": "Появилась",
    "created": "Появилась",
    "expired": "Истекла",
}


def _ru_status(value: object) -> str:
    text = str(value or "")
    return _RU_STATUS.get(text, _RU_STATUS.get(text.lower(), text or "n/a"))


def _ru_direction(value: object) -> str:
    text = str(value or "").upper()
    return {"LONG": "Лонг", "SHORT": "Шорт"}.get(text, text or "n/a")


def _ru_job(value: object) -> str:
    text = str(value or "")
    return _RU_JOB.get(text, text or "n/a")


def _ru_event(value: object) -> str:
    text = str(value or "")
    return _RU_EVENT.get(text.lower(), text or "n/a")


def _fromjson(value: object) -> Any:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return None


def _outcome(signal: object, horizon_minutes: int = 30) -> Any:
    for item in getattr(signal, "outcomes", None) or []:
        if item.horizon_minutes == horizon_minutes:
            return item
    return None


def _ago(value: object) -> str:
    if not isinstance(value, datetime):
        return "n/a"
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    seconds = int((datetime.now(UTC) - aware).total_seconds())
    if seconds < 0:
        return "только что"
    if seconds < 60:
        return f"{seconds} с назад"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    return f"{hours // 24} дн назад"


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
