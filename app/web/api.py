from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder

from app.jobs.models import DOWNLOAD_HISTORY, RUN_BACKTEST, RUN_HYPEROPT
from app.jobs.queue import JobQueue
from app.web.auth import require_web_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_web_auth)])


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
    strategy_key = str(params.get("strategy_key", "micro_stop_hunt_reclaim"))
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
            },
        )
    )


@router.post("/strategy-lab/hyperopt/run")
async def api_run_hyperopt(request: Request):
    params = await _request_params(request)
    strategy_key = str(params.get("strategy_key", "micro_stop_hunt_reclaim"))
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
            },
        )
    )


async def _request_params(request: Request) -> dict[str, object]:
    if request.query_params:
        return dict(request.query_params)
    form = await request.form()
    return dict(form)
