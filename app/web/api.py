from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder

from app.data.repositories import RuntimeSettingsRepository
from app.jobs.models import (
    DOWNLOAD_HISTORY,
    RUN_BACKTEST,
    RUN_DENSITY_ANALYSIS,
    RUN_HYPEROPT,
    TRAIN_ML_MODEL,
)
from app.jobs.queue import JobQueue
from app.web.auth import require_api_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_auth)])


@router.get("/status")
async def api_status(request: Request):
    return jsonable_encoder(await request.app.state.status_service.dashboard())


@router.get("/signals")
async def api_signals(request: Request, limit: int = 50, offset: int = 0):
    signals = await request.app.state.signal_service.recent(limit=min(limit, 200), offset=offset)
    return jsonable_encoder(signals)


@router.get("/paper/profiles")
async def api_paper_profiles(request: Request):
    return jsonable_encoder(await request.app.state.paper_service.profiles())


@router.get("/paper/trades/open")
async def api_open_paper_trades(request: Request, limit: int = 100):
    trades = await request.app.state.paper_service.trades(status="OPEN", limit=min(limit, 500))
    return jsonable_encoder(trades)


@router.get("/paper/trades/closed")
async def api_closed_paper_trades(request: Request, limit: int = 100):
    trades = await request.app.state.paper_service.trades(status="CLOSED", limit=min(limit, 500))
    return jsonable_encoder(trades)


@router.get("/analytics/summary")
async def api_analytics_summary(request: Request):
    return jsonable_encoder(await request.app.state.analytics_service.summary())


@router.get("/performance")
async def api_performance(request: Request):
    return jsonable_encoder(await request.app.state.performance_service.snapshot())


@router.get("/charts/trade/{trade_id}")
async def api_trade_chart(request: Request, trade_id: int):
    return jsonable_encoder(await request.app.state.chart_service.trade_chart(trade_id))


@router.get("/charts/signal/{signal_id}")
async def api_signal_chart(request: Request, signal_id: int):
    return jsonable_encoder(await request.app.state.chart_service.signal_chart(signal_id))


@router.get("/strategy-lab/strategies")
async def api_strategy_lab_strategies(request: Request):
    return jsonable_encoder(await request.app.state.strategy_lab_service.strategies())


@router.get("/strategy-lab/backtests")
async def api_strategy_lab_backtests(request: Request):
    return jsonable_encoder(await request.app.state.strategy_lab_service.backtest_runs())


@router.get("/strategy-lab/jobs")
async def api_strategy_lab_jobs(request: Request):
    return jsonable_encoder(await request.app.state.strategy_lab_service.jobs())


@router.get("/strategy-lab/data")
async def api_strategy_lab_data(request: Request):
    return jsonable_encoder(await request.app.state.strategy_lab_service.data_coverage())


@router.get("/strategy-lab/diagnostics")
async def api_strategy_lab_diagnostics(request: Request):
    return jsonable_encoder(await request.app.state.strategy_lab_service.diagnostics())


@router.get("/strategy-lab/instances")
async def api_strategy_lab_instances(request: Request):
    return jsonable_encoder(await request.app.state.strategy_lab_service.instances())


@router.get("/strategy-lab/compare")
async def api_strategy_lab_compare(request: Request):
    return jsonable_encoder(await request.app.state.strategy_lab_service.compare())


@router.get("/strategy-lab/ml/status")
async def api_strategy_lab_ml_status(request: Request):
    return jsonable_encoder(await request.app.state.strategy_lab_service.ml_status())


@router.get("/strategy-lab/density/events")
async def api_strategy_lab_density_events(request: Request, symbol: str | None = None):
    return jsonable_encoder(await request.app.state.strategy_lab_service.density_events(symbol=symbol))


@router.post("/strategy-lab/instances/{instance_id}/toggle")
async def api_toggle_strategy_instance(request: Request, instance_id: str):
    instance = request.app.state.settings.strategy_instances.instances.get(instance_id)
    if instance is None:
        return {"ok": False, "error": "instance_not_found"}
    instance.enabled = not instance.enabled
    async with request.app.state.database.session() as session:
        await RuntimeSettingsRepository(session).set(
            f"strategy_instances.instances.{instance_id}.enabled", instance.enabled
        )
    return {"ok": True, "instance_id": instance_id, "enabled": instance.enabled}


@router.post("/strategy-lab/instances/{instance_id}/settings")
async def api_update_strategy_instance_settings(request: Request, instance_id: str):
    instance = request.app.state.settings.strategy_instances.instances.get(instance_id)
    if instance is None:
        return {"ok": False, "error": "instance_not_found"}
    params = await _request_params(request)
    updates = _strategy_instance_updates(params)
    if not updates:
        return {"ok": False, "error": "no_supported_settings"}
    async with request.app.state.database.session() as session:
        repo = RuntimeSettingsRepository(session)
        for key, value in updates.items():
            if key == "min_score":
                instance.min_score = int(value)
            elif key == "paper_profile":
                instance.paper_profile = str(value)
            elif key == "symbols":
                instance.symbols = str(value)
            elif key == "enabled":
                instance.enabled = bool(value)
            elif key.startswith("config."):
                config_key = key.removeprefix("config.")
                instance.config[config_key] = value
            await repo.set(f"strategy_instances.instances.{instance_id}.{key}", value)
    return {
        "ok": True,
        "instance_id": instance_id,
        "updates": updates,
        "instance": jsonable_encoder(instance),
    }


@router.post("/strategy-lab/history/download")
async def api_download_history(request: Request):
    params = await _request_params(request)
    symbol = str(params.get("symbol", "BTCUSDT")).upper()
    timeframe = str(params.get("timeframe", "1m"))
    days = int(params.get("days", 30))
    return jsonable_encoder(
        await JobQueue(request.app.state.database).enqueue(
            DOWNLOAD_HISTORY, {"symbol": symbol, "timeframe": timeframe, "days": days}
        )
    )


@router.post("/strategy-lab/backtests/run")
async def api_run_backtest(request: Request):
    params = await _request_params(request)
    strategy_key, instance_params = _strategy_params(request, params)
    symbol = str(params.get("symbol", "BTCUSDT")).upper()
    timeframe = str(params.get("timeframe", "1m"))
    days = int(params.get("days", 30))
    return jsonable_encoder(
        await JobQueue(request.app.state.database).enqueue(
            RUN_BACKTEST,
            {
                "strategy_key": strategy_key,
                "symbol": symbol,
                "timeframe": timeframe,
                "days": days,
                "params": instance_params,
                "strategy_instance_id": params.get("strategy_instance_id"),
            },
        )
    )


@router.post("/strategy-lab/hyperopt/run")
async def api_run_hyperopt(request: Request):
    params = await _request_params(request)
    strategy_key, instance_params = _strategy_params(request, params)
    symbol = str(params.get("symbol", "BTCUSDT")).upper()
    timeframe = str(params.get("timeframe", "1m"))
    days = int(params.get("days", 30))
    return jsonable_encoder(
        await JobQueue(request.app.state.database).enqueue(
            RUN_HYPEROPT,
            {
                "strategy_key": strategy_key,
                "symbol": symbol,
                "timeframe": timeframe,
                "days": days,
                "params": instance_params,
                "strategy_instance_id": params.get("strategy_instance_id"),
            },
        )
    )


@router.post("/strategy-lab/ml/train")
async def api_train_ml_model(request: Request):
    params = await _request_params(request)
    return jsonable_encoder(
        await JobQueue(request.app.state.database).enqueue(
            TRAIN_ML_MODEL,
            {"model_type": str(params.get("model_type", "heuristic_gbdt_proxy"))},
        )
    )


@router.post("/strategy-lab/density/analyze")
async def api_run_density_analysis(request: Request):
    params = await _request_params(request)
    return jsonable_encoder(
        await JobQueue(request.app.state.database).enqueue(
            RUN_DENSITY_ANALYSIS,
            {"symbol": params.get("symbol") or None, "limit": int(params.get("limit", 500))},
        )
    )


async def _request_params(request: Request) -> dict[str, object]:
    if request.query_params:
        return dict(request.query_params)
    raw = (await request.body()).decode()
    return {key: values[0] for key, values in parse_qs(raw).items()}


def _strategy_params(request: Request, params: dict[str, object]) -> tuple[str, dict]:
    instance_id = str(params.get("strategy_instance_id") or "")
    if instance_id:
        instance = request.app.state.settings.strategy_instances.instances.get(instance_id)
        if instance is not None:
            merged = dict(instance.config)
            for key in ("min_score", "stop_loss_pct", "take_profit_pct", "max_holding_minutes"):
                if key in params:
                    merged[key] = _coerce_value(params[key])
            return instance.strategy_key, merged
    return str(params.get("strategy_key", "micro_stop_hunt_reclaim")), {}


def _strategy_instance_updates(params: dict[str, object]) -> dict[str, object]:
    supported = {
        "min_score",
        "paper_profile",
        "symbols",
        "enabled",
        "config.min_density_usd",
        "config.max_distance_pct",
        "config.min_lifetime_sec",
        "config.require_absorption",
        "config.require_trend_alignment",
        "config.volume_spike_multiplier",
        "config.stop_behind_density_pct",
        "config.take_profit_rr",
        "config.max_holding_minutes",
    }
    updates = {}
    for key, value in params.items():
        if key in supported:
            updates[key] = _coerce_value(value)
    return updates


def _coerce_value(value: object) -> object:
    text = str(value).strip()
    if text.lower() in {"true", "on", "1", "yes"}:
        return True
    if text.lower() in {"false", "off", "0", "no"}:
        return False
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text
