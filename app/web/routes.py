from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.web.auth import (
    login_user,
    logout_user,
    require_web_auth,
    verify_credentials,
    web_credentials_configured,
)

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None, next: str = "/"):
    return request.app.state.templates.TemplateResponse(
        request,
        "login.html",
        {
            "page": "login",
            "error": error,
            "next": next,
            "configured": web_credentials_configured(),
        },
    )


@router.post("/login")
async def login_submit(request: Request):
    raw = (await request.body()).decode()
    data = {key: values[0] for key, values in parse_qs(raw).items()}
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))
    next_url = str(data.get("next", "/")) or "/"
    if not web_credentials_configured():
        return RedirectResponse("/login?error=not_configured", status_code=303)
    if not verify_credentials(username, password):
        return RedirectResponse(f"/login?error=invalid&next={next_url}", status_code=303)
    login_user(request, username)
    return RedirectResponse(next_url if next_url.startswith("/") else "/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _: str = Depends(require_web_auth)):
    status = await request.app.state.status_service.dashboard()
    return request.app.state.templates.TemplateResponse(
        request, "dashboard.html", {"page": "dashboard", "status": status}
    )


@router.get("/signals", response_class=HTMLResponse)
async def signals(request: Request, _: str = Depends(require_web_auth)):
    rows = await request.app.state.signal_service.recent(limit=50)
    return request.app.state.templates.TemplateResponse(
        request, "signals.html", {"page": "signals", "signals": rows}
    )


@router.get("/paper", response_class=HTMLResponse)
async def paper(request: Request, _: str = Depends(require_web_auth)):
    profiles = await request.app.state.paper_service.profiles()
    return request.app.state.templates.TemplateResponse(
        request, "paper.html", {"page": "paper", "profiles": profiles}
    )


@router.get("/trades", response_class=HTMLResponse)
async def trades(request: Request, _: str = Depends(require_web_auth)):
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
async def analytics(request: Request, _: str = Depends(require_web_auth)):
    summary = await request.app.state.analytics_service.summary()
    return request.app.state.templates.TemplateResponse(
        request, "analytics.html", {"page": "analytics", "summary": summary}
    )


@router.get("/performance", response_class=HTMLResponse)
async def performance(request: Request, _: str = Depends(require_web_auth)):
    snapshot = await request.app.state.performance_service.snapshot()
    return request.app.state.templates.TemplateResponse(
        request, "performance.html", {"page": "performance", "performance": snapshot}
    )


@router.get("/strategy-lab", response_class=HTMLResponse)
async def strategy_lab(request: Request, _: str = Depends(require_web_auth)):
    lab = await request.app.state.strategy_lab_service.overview()
    return request.app.state.templates.TemplateResponse(
        request, "strategy_lab.html", {"page": "strategy_lab", "lab": lab, "section": "overview"}
    )


@router.get("/strategy-lab/{section}", response_class=HTMLResponse)
async def strategy_lab_section(
    request: Request,
    section: str,
    _: str = Depends(require_web_auth),
):
    allowed = {"strategies", "instances", "backtests", "hyperopt", "compare", "density"}
    lab = await request.app.state.strategy_lab_service.overview()
    return request.app.state.templates.TemplateResponse(
        request,
        "strategy_lab.html",
        {"page": "strategy_lab", "lab": lab, "section": section if section in allowed else "overview"},
    )


@router.get("/analytics/diagnostics", response_class=HTMLResponse)
async def diagnostics(request: Request, _: str = Depends(require_web_auth)):
    diagnostics_data = await request.app.state.strategy_lab_service.diagnostics()
    return request.app.state.templates.TemplateResponse(
        request,
        "diagnostics.html",
        {"page": "analytics", "diagnostics": diagnostics_data},
    )


@router.get("/analytics/why-losing", response_class=HTMLResponse)
async def why_losing(request: Request, _: str = Depends(require_web_auth)):
    diagnostics_data = await request.app.state.strategy_lab_service.diagnostics()
    return request.app.state.templates.TemplateResponse(
        request,
        "diagnostics.html",
        {"page": "analytics", "diagnostics": diagnostics_data},
    )
