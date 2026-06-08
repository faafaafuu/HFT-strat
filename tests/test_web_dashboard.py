from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.config import DatabaseConfig, Settings, StorageConfig
from app.web.main import create_app


class _StatusService:
    async def dashboard(self):
        return {
            "online": True,
            "pairs_count": 0,
            "signals_today": 0,
            "signals_week": 0,
            "best_pattern": None,
            "best_pair": None,
            "uptime": timedelta(),
            "last_heartbeat": None,
            "active_websocket_connections": 0,
            "selected_symbols": [],
            "last_signal_time": None,
            "memory_mb": 0.0,
            "active_tasks": 0,
            "db_size_mb": 0.0,
            "open_paper_trades": 0,
        }


class _SignalService:
    async def recent(self, limit: int = 20, offset: int = 0):
        return []


class _PaperService:
    async def profiles(self):
        return []

    async def trades(self, profile_key=None, status=None, limit: int = 50):
        return []


class _AnalyticsService:
    async def summary(self):
        return {
            "signals": {"total_signals": 0, "winrate_tp1_30m": 0},
            "profiles": [],
            "paper": {"trades": 0, "net_pnl": 0, "profit_factor": 0},
            "by_symbol": [],
            "by_pattern": [],
            "by_score": [],
            "by_hour": [],
        }


class _PerformanceService:
    async def snapshot(self):
        return {
            "ram_mb": 0,
            "active_tasks": 0,
            "ws_connections": 0,
            "selected_symbols": 0,
            "db_size_mb": 0,
            "heartbeat_exists": False,
            "signal_cycle_ms": None,
            "orderbook_processing_ms": None,
            "db_write_latency_ms": None,
            "telegram_callback_latency_ms": None,
            "queue_sizes": {},
        }


def _app(tmp_path: Path, monkeypatch, auth: bool = True):
    if auth:
        monkeypatch.setenv("WEB_USERNAME", "admin")
        monkeypatch.setenv("WEB_PASSWORD", "secret")
    else:
        monkeypatch.delenv("WEB_USERNAME", raising=False)
        monkeypatch.delenv("WEB_PASSWORD", raising=False)
    settings = Settings(
        database=DatabaseConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'bot.sqlite3'}"),
        storage=StorageConfig(
            data_dir=str(tmp_path / "data"),
            logs_dir=str(tmp_path / "logs"),
            backups_dir=str(tmp_path / "backups"),
        ),
    )
    app = create_app(settings=settings, init_database=False)
    app.state.status_service = _StatusService()
    app.state.signal_service = _SignalService()
    app.state.paper_service = _PaperService()
    app.state.analytics_service = _AnalyticsService()
    app.state.performance_service = _PerformanceService()
    return app


async def _get(app, path: str, auth: tuple[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, auth=auth)


@pytest.mark.asyncio
async def test_web_auth_required(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch), "/")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_web_requires_configured_credentials(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch, auth=False), "/", auth=("admin", "secret"))

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_status_page_works_on_empty_data(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch), "/", auth=("admin", "secret"))

    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Signals Today" in response.text


@pytest.mark.asyncio
async def test_paper_profiles_page_works_on_empty_data(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch), "/paper", auth=("admin", "secret"))

    assert response.status_code == 200
    assert "Paper Trading" in response.text


@pytest.mark.asyncio
async def test_analytics_endpoint_works_on_empty_data(tmp_path: Path, monkeypatch) -> None:
    response = await _get(
        _app(tmp_path, monkeypatch), "/api/analytics/summary", auth=("admin", "secret")
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["signals"]["total_signals"] == 0
    assert payload["paper"]["trades"] == 0

