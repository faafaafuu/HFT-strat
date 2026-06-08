from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder

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

