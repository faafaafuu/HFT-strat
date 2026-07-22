import json
from contextlib import asynccontextmanager
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

    async def recent_with_outcomes(self, limit: int = 100, offset: int = 0):
        return []

    async def detail(self, signal_id: int):
        return None


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
            "hyperopt": {"job_id": None, "rows": [], "by_timeframe": []},
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

    async def section(self, name: str):
        overview = await self.overview()
        overview["profiles"] = []
        return overview

    async def strategies(self):
        return []

    async def strategy_profiles(self):
        return []

    async def instances(self):
        return []

    async def jobs(self):
        return []

    def invalidate_cache(self):
        return None


class _StrategyLabServiceWithStrategies(_StrategyLabService):
    """Same stub, but with a strategy present so the pickers have something to render."""

    async def strategies(self):
        return [
            {
                "key": "channel_4_touch",
                "name": "Канал: вход на 4-м касании",
                "enabled": True,
                "profiles": [],
                "instances": [],
                "description": "Вход на четвёртом касании границы канала.",
                "config_fields": {"stop_pct": 1.0},
            }
        ]

    spaces: dict = {}

    async def section(self, name: str):
        data = await super().section(name)
        data["strategies"] = await self.strategies()
        data["symbols"] = [{"symbol": "BTCUSDT", "has_candles": True}]
        data["cache"] = {"total": 0, "reused": 0, "rows": []}
        data["search_spaces"] = self.spaces
        return data


class _RecordingSession:
    def __init__(self, saved: dict) -> None:
        self.saved = saved

    async def execute(self, statement):
        values = statement.compile().params
        self.saved[values["key"]] = json.loads(values["value_json"])
        return None


class _RecordingDatabase:
    """Captures runtime-setting writes without touching a real database."""

    def __init__(self) -> None:
        self.saved: dict = {}

    @asynccontextmanager
    async def session(self):
        yield _RecordingSession(self.saved)


class _ChartService:
    async def trade_chart(self, trade_id: int):
        return None

    async def signal_chart(self, signal_id: int):
        return None


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
    app.state.chart_service = _ChartService()
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
    assert "Сводка" in response.text
    assert "Сигналов сегодня" in response.text


@pytest.mark.asyncio
async def test_paper_profiles_page_works_on_empty_data(tmp_path: Path, monkeypatch) -> None:
    response = await _authed_get(_app(tmp_path, monkeypatch), "/paper")

    assert response.status_code == 200
    assert "Paper-торговля" in response.text


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
    assert "Лаборатория стратегий" in response.text


@pytest.mark.asyncio
async def test_strategy_lab_sections_render_distinct_content(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)

    strategies = await _authed_get(app, "/strategy-lab/strategies")
    density = await _authed_get(app, "/strategy-lab/density")

    assert strategies.status_code == 200
    assert density.status_code == 200
    assert "Профили" in strategies.text
    assert "Профили" not in density.text
    assert "Последние события" in density.text


@pytest.mark.asyncio
async def test_every_lab_section_renders(tmp_path: Path, monkeypatch) -> None:
    """Template errors only surface on render, so each tab is exercised."""
    from app.web.routes import LAB_SECTIONS

    app = _app(tmp_path, monkeypatch)
    app.state.strategy_lab_service = _StrategyLabServiceWithStrategies()

    for section, _label in LAB_SECTIONS:
        response = await _authed_get(app, f"/strategy-lab/{section}")
        assert response.status_code == 200, f"секция {section} не отрисовалась"


@pytest.mark.asyncio
async def test_search_space_renders_its_options(tmp_path: Path, monkeypatch) -> None:
    """`field.values` would resolve to the dict method instead of the grid values."""
    app = _app(tmp_path, monkeypatch)
    service = _StrategyLabServiceWithStrategies()
    service.spaces = {
        "channel_4_touch": {
            "name": "Канал",
            "fields": [{"key": "stop_pct", "options": [0.3, 1.0], "count": 2}],
            "combinations": 2,
        }
    }
    app.state.strategy_lab_service = service

    response = await _authed_get(app, "/strategy-lab/hyperopt")

    assert response.status_code == 200
    assert "0.3 %" in response.text
    assert "1 %" in response.text


@pytest.mark.asyncio
async def test_backtest_and_hyperopt_forms_list_strategies(tmp_path: Path, monkeypatch) -> None:
    """The picker reads lab.strategies; reading a bare `strategies` renders it empty."""
    app = _app(tmp_path, monkeypatch)
    app.state.strategy_lab_service = _StrategyLabServiceWithStrategies()

    backtests = await _authed_get(app, "/strategy-lab/backtests")
    hyperopt = await _authed_get(app, "/strategy-lab/hyperopt")

    for response in (backtests, hyperopt):
        assert response.status_code == 200
        assert 'name="strategy_key"' in response.text
        assert "Канал: вход на 4-м касании" in response.text
        assert "стратегии не загружены" not in response.text


@pytest.mark.asyncio
async def test_strategy_lab_htmx_request_returns_fragment_only(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        await client.post(
            "/login",
            content="username=admin&password=secret&next=/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = await client.get(
            "/strategy-lab/instances", headers={"HX-Request": "true"}
        )

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Пресеты стратегий" in response.text


@pytest.mark.asyncio
async def test_strategy_toggle_flips_state_and_persists(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    app.state.database = _RecordingDatabase()
    settings = app.state.settings
    assert settings.strategy_toggles.is_enabled("density_strategy")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        await client.post(
            "/login",
            content="username=admin&password=secret&next=/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = await client.post(
            "/actions/strategies/density_strategy/toggle", headers={"HX-Request": "true"}
        )

    assert response.status_code == 200
    assert not settings.strategy_toggles.is_enabled("density_strategy")
    assert app.state.database.saved["strategy_toggles.disabled"] == ["density_strategy"]


def test_backtest_payload_carries_explicit_min_score() -> None:
    """The live signals.min_score is tuned for Telegram and would empty every backtest."""
    from types import SimpleNamespace

    from app.jobs.models import RUN_BACKTEST
    from app.web.actions import _job_payload

    settings = Settings()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))

    payload = _job_payload(
        request,
        RUN_BACKTEST,
        {"strategy_key": "channel_4_touch", "symbol": "btcusdt", "min_score": "6", "days": "90"},
    )

    assert payload["params"]["min_score"] == 6
    assert payload["symbol"] == "BTCUSDT"
    assert payload["timeframe"] == "5m"


def test_hyperopt_payload_joins_checked_timeframes() -> None:
    from types import SimpleNamespace

    from app.jobs.models import RUN_HYPEROPT
    from app.web.actions import _job_payload

    settings = Settings()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))

    payload = _job_payload(
        request,
        RUN_HYPEROPT,
        {"strategy_key": "channel_4_touch", "timeframes": ["5m", "1h"], "limit": "40"},
    )

    assert payload["timeframe"] == "5m,1h"
    assert payload["limit"] == 40


@pytest.mark.asyncio
async def test_disabled_strategy_stops_generating_signals() -> None:
    from app.strategies.registry import default_registry

    settings = Settings()
    registry = default_registry(settings)
    settings.strategy_toggles.set_enabled("micro_stop_hunt_reclaim", False)

    descriptors = {item.key: item for item in registry.descriptors(settings)}

    assert descriptors["micro_stop_hunt_reclaim"].enabled is False
    assert descriptors["failed_breakout_fade"].enabled is True


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


def test_instance_update_parser_accepts_any_strategy_config_key() -> None:
    """The editor is built from the strategy's own fields, so no density whitelist."""
    from app.web.api import _strategy_instance_updates

    updates = _strategy_instance_updates(
        {"config.min_rr": "1.5", "config.max_bars_wait_touch": "120", "junk": "x"}
    )

    assert updates == {"config.min_rr": 1.5, "config.max_bars_wait_touch": 120}


def test_run_form_params_reach_the_strategy() -> None:
    """Form fields other than the run controls become strategy/exit parameters."""
    from types import SimpleNamespace

    from app.web.api import _strategy_params

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace()))
    )
    key, params = _strategy_params(
        request,
        {
            "strategy_key": "channel_4_touch",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "days": "720",
            "limit": "50",
            "stop_pct": "0.5",
            "trailing_enabled": "true",
            "take_pct": "",
        },
    )

    assert key == "channel_4_touch"
    # Run controls are stripped, blanks are dropped, the rest is coerced.
    assert params == {"stop_pct": 0.5, "trailing_enabled": True}
