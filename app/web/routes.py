from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.web.auth import require_web_auth

router = APIRouter(dependencies=[Depends(require_web_auth)])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    status = await request.app.state.status_service.dashboard()
    return request.app.state.templates.TemplateResponse(
        request, "dashboard.html", {"page": "dashboard", "status": status}
    )


@router.get("/signals", response_class=HTMLResponse)
async def signals(request: Request):
    rows = await request.app.state.signal_service.recent(limit=50)
    return request.app.state.templates.TemplateResponse(
        request, "signals.html", {"page": "signals", "signals": rows}
    )


@router.get("/paper", response_class=HTMLResponse)
async def paper(request: Request):
    profiles = await request.app.state.paper_service.profiles()
    return request.app.state.templates.TemplateResponse(
        request, "paper.html", {"page": "paper", "profiles": profiles}
    )


@router.get("/trades", response_class=HTMLResponse)
async def trades(request: Request):
    open_trades = await request.app.state.paper_service.trades(status="OPEN", limit=100)
    closed_trades = await request.app.state.paper_service.trades(status="CLOSED", limit=100)
    return request.app.state.templates.TemplateResponse(
        request,
        "trades.html",
        {
            "page": "trades",
            "open_trades": open_trades,
            "closed_trades": closed_trades,
        },
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request):
    summary = await request.app.state.analytics_service.summary()
    return request.app.state.templates.TemplateResponse(
        request, "analytics.html", {"page": "analytics", "summary": summary}
    )


@router.get("/performance", response_class=HTMLResponse)
async def performance(request: Request):
    snapshot = await request.app.state.performance_service.snapshot()
    return request.app.state.templates.TemplateResponse(
        request, "performance.html", {"page": "performance", "performance": snapshot}
    )

