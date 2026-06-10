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


class _StrategyLabService:
    async def overview(self):
        return {
            "strategies": [],
            "backtests": [],
            "jobs": [],
            "coverage": [],
            "instances": [],
            "density_events": [],
            "density_summary": [],
            "compare": {"profiles": [], "backtests": []},
            "ml_status": {"active": False, "reason": "no_active_model"},
            "diagnostics": {
                "by_strategy": [],
                "by_instance": [],
                "by_profile": [],
                "by_pattern": [],
                "by_symbol": [],
                "by_score": [],
                "by_hour": [],
                "by_status": [],
            },
        }

    async def diagnostics(self):
        return (await self.overview())["diagnostics"]


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
    app.state.strategy_lab_service = _StrategyLabService()
    return app


async def _get(app, path: str, auth: tuple[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, auth=auth)


async def _authed_get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        login = await client.post(
            "/login",
            content="username=admin&password=secret&next=/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert login.status_code == 303
        return await client.get(path)


@pytest.mark.asyncio
async def test_web_auth_required(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch), "/")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_health_is_public(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch), "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_web_requires_configured_credentials(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch, auth=False), "/")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_login_page_works_without_browser_popup(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch), "/login")

    assert response.status_code == 200
    assert "username" in response.text
    assert "password" in response.text


@pytest.mark.asyncio
async def test_status_page_works_on_empty_data(tmp_path: Path, monkeypatch) -> None:
    response = await _authed_get(_app(tmp_path, monkeypatch), "/")

    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Signals Today" in response.text


@pytest.mark.asyncio
async def test_paper_profiles_page_works_on_empty_data(tmp_path: Path, monkeypatch) -> None:
    response = await _authed_get(_app(tmp_path, monkeypatch), "/paper")

    assert response.status_code == 200
    assert "Paper Trading" in response.text


@pytest.mark.asyncio
async def test_analytics_endpoint_works_on_empty_data(tmp_path: Path, monkeypatch) -> None:
    response = await _authed_get(_app(tmp_path, monkeypatch), "/api/analytics/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["signals"]["total_signals"] == 0
    assert payload["paper"]["trades"] == 0


@pytest.mark.asyncio
async def test_strategy_lab_page_works_on_empty_data(tmp_path: Path, monkeypatch) -> None:
    response = await _authed_get(_app(tmp_path, monkeypatch), "/strategy-lab")

    assert response.status_code == 200
    assert "Strategy Lab" in response.text


@pytest.mark.asyncio
async def test_api_auth_required(tmp_path: Path, monkeypatch) -> None:
    response = await _get(_app(tmp_path, monkeypatch), "/api/analytics/summary")

    assert response.status_code == 401


def test_strategy_instance_update_parser_coerces_supported_values() -> None:
    from app.web.api import _strategy_instance_updates

    updates = _strategy_instance_updates(
        {
            "min_score": "8",
            "config.min_density_usd": "1000000",
            "config.require_absorption": "true",
            "unsupported": "ignored",
        }
    )

    assert updates == {
        "min_score": 8,
        "config.min_density_usd": 1_000_000,
        "config.require_absorption": True,
    }
