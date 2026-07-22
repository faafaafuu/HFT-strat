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

LAB_SECTIONS = [
    ("overview", "Обзор"),
    ("strategies", "Стратегии"),
    ("instances", "Пресеты"),
    ("backtests", "Бэктесты"),
    ("hyperopt", "Гипероптимизация"),
    ("compare", "Сравнение"),
    ("density", "Плотности"),
]
LAB_SECTION_KEYS = {key for key, _ in LAB_SECTIONS}


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


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
async def signals(
    request: Request,
    symbol: str = "",
    direction: str = "",
    strategy: str = "",
    min_score: int = 0,
    limit: int = 100,
    _: str = Depends(require_web_auth),
):
    service = request.app.state.signal_service
    rows = await service.recent_with_outcomes(limit=min(limit, 500))
    filtered = [
        row
        for row in rows
        if (not symbol or row.symbol == symbol)
        and (not direction or row.direction == direction)
        and (not strategy or (row.strategy_key or "") == strategy)
        and (row.score or 0) >= min_score
    ]
    facets = {
        "symbols": sorted({row.symbol for row in rows if row.symbol}),
        "strategies": sorted({row.strategy_key for row in rows if row.strategy_key}),
    }
    context = {
        "page": "signals",
        "signals": filtered,
        "total": len(rows),
        "facets": facets,
        "filters": {
            "symbol": symbol,
            "direction": direction,
            "strategy": strategy,
            "min_score": min_score,
        },
        "stats": _signal_stats(filtered),
    }
    template = "partials/signal_table.html" if _is_htmx(request) else "signals.html"
    return request.app.state.templates.TemplateResponse(request, template, context)


@router.get("/signals/{signal_id}", response_class=HTMLResponse)
async def signal_detail(request: Request, signal_id: int, _: str = Depends(require_web_auth)):
    signal = await request.app.state.signal_service.detail(signal_id)
    chart = await request.app.state.chart_service.signal_chart(signal_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "signal_detail.html",
        {
            "page": "signals",
            "signal": signal,
            "chart": chart,
            "chart_json": _chart_payload(chart),
        },
    )


@router.get("/paper", response_class=HTMLResponse)
async def paper(request: Request, _: str = Depends(require_web_auth)):
    profiles = await request.app.state.paper_service.profiles()
    lab = request.app.state.strategy_lab_service
    return request.app.state.templates.TemplateResponse(
        request,
        "paper.html",
        {
            "page": "paper",
            "profiles": profiles,
            "settings": request.app.state.settings,
            "instances": await lab.instances(),
            "strategy_profiles": await lab.strategy_profiles(),
            "strategies": await lab.strategies(),
            "open_trades": await request.app.state.paper_service.trades(status="OPEN", limit=50),
        },
    )


@router.get("/trades", response_class=HTMLResponse)
async def trades(
    request: Request,
    symbol: str = "",
    profile: str = "",
    strategy: str = "",
    status: str = "",
    _: str = Depends(require_web_auth),
):
    service = request.app.state.paper_service
    open_trades = await service.trades(status="OPEN", limit=200)
    closed_trades = await service.trades(status="CLOSED", limit=300)

    def keep(row):
        return (
            (not symbol or row.symbol == symbol)
            and (not profile or row.profile_key == profile)
            and (not strategy or (row.strategy_key or "") == strategy)
            and (not status or row.status == status)
        )

    everything = open_trades + closed_trades
    facets = {
        "symbols": sorted({row.symbol for row in everything if row.symbol}),
        "profiles": sorted({row.profile_key for row in everything if row.profile_key}),
        "strategies": sorted({row.strategy_key for row in everything if row.strategy_key}),
        "statuses": sorted({row.status for row in everything if row.status}),
    }
    context = {
        "page": "trades",
        "open_trades": [row for row in open_trades if keep(row)],
        "closed_trades": [row for row in closed_trades if keep(row)],
        "facets": facets,
        "filters": {
            "symbol": symbol,
            "profile": profile,
            "strategy": strategy,
            "status": status,
        },
    }
    context["stats"] = _trade_stats(context["closed_trades"])
    template = "partials/trade_lists.html" if _is_htmx(request) else "trades.html"
    return request.app.state.templates.TemplateResponse(request, template, context)


@router.get("/chart", response_class=HTMLResponse)
async def symbol_chart(
    request: Request,
    symbol: str = "",
    days: int = 30,
    _: str = Depends(require_web_auth),
):
    service = request.app.state.chart_service
    symbols = await service.traded_symbols(days=days)
    selected = symbol or (symbols[0]["symbol"] if symbols else "")
    chart = await service.symbol_chart(selected, days=days) if selected else None
    return request.app.state.templates.TemplateResponse(
        request,
        "symbol_chart.html",
        {
            "page": "chart",
            "symbols": symbols,
            "selected": selected,
            "days": days,
            "chart": chart,
            "chart_json": _chart_payload(chart),
            "trades": (chart or {}).get("trades", []),
        },
    )


@router.get("/backtests/{run_id}", response_class=HTMLResponse)
async def backtest_detail(request: Request, run_id: int, _: str = Depends(require_web_auth)):
    chart = await request.app.state.chart_service.backtest_chart(run_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "backtest_detail.html",
        {
            "page": "strategy_lab",
            "chart": chart,
            "run": (chart or {}).get("run"),
            "trades": (chart or {}).get("trades", []),
            "chart_json": _chart_payload(chart),
        },
    )


@router.get("/trades/{trade_id}", response_class=HTMLResponse)
async def trade_detail(request: Request, trade_id: int, _: str = Depends(require_web_auth)):
    chart = await request.app.state.chart_service.trade_chart(trade_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "trade_detail.html",
        {
            "page": "trades",
            "chart": chart,
            "chart_json": _chart_payload(chart),
            "trade": (chart or {}).get("trade"),
        },
    )


def _chart_payload(chart: dict | None) -> dict:
    """Only the plot-facing keys — the trade block holds datetimes tojson cannot encode."""
    if not chart:
        return {"series": [], "levels": [], "markers": [], "channels": []}
    return {
        "series": chart.get("series", []),
        "levels": chart.get("levels", []),
        "markers": chart.get("markers", []),
        "channels": chart.get("channels", []),
        "source": chart.get("source"),
        "timeframe": chart.get("timeframe"),
    }


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
    return await _render_lab_section(request, "overview")


@router.get("/strategy-lab/{section}", response_class=HTMLResponse)
async def strategy_lab_section(
    request: Request,
    section: str,
    _: str = Depends(require_web_auth),
):
    return await _render_lab_section(request, section)


async def _render_lab_section(request: Request, section: str):
    section = section if section in LAB_SECTION_KEYS else "overview"
    data = await request.app.state.strategy_lab_service.section(section)
    context = {
        "page": "strategy_lab",
        "section": section,
        "sections": LAB_SECTIONS,
        "lab": data,
    }
    template = (
        f"lab/{section}.html" if _is_htmx(request) else "strategy_lab.html"
    )
    return request.app.state.templates.TemplateResponse(request, template, context)


@router.get("/fragments/jobs", response_class=HTMLResponse)
async def fragment_jobs(request: Request, _: str = Depends(require_web_auth)):
    jobs = await request.app.state.strategy_lab_service.jobs()
    return request.app.state.templates.TemplateResponse(
        request, "partials/jobs_table.html", {"jobs": jobs}
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


def _signal_stats(rows: list) -> dict[str, object]:
    if not rows:
        return {"count": 0, "long": 0, "short": 0, "avg_score": 0.0, "measured": 0, "hit_rate": 0.0}
    measured = [row for row in rows if _outcome_at(row, 30) is not None]
    hits = [row for row in measured if getattr(_outcome_at(row, 30), "hit_tp_1_0", False)]
    return {
        "count": len(rows),
        "long": len([row for row in rows if row.direction == "LONG"]),
        "short": len([row for row in rows if row.direction == "SHORT"]),
        "avg_score": sum(row.score or 0 for row in rows) / len(rows),
        "measured": len(measured),
        "hit_rate": len(hits) / len(measured) * 100 if measured else 0.0,
    }


def _outcome_at(signal, horizon_minutes: int):
    for outcome in getattr(signal, "outcomes", None) or []:
        if outcome.horizon_minutes == horizon_minutes:
            return outcome
    return None


def _trade_stats(rows: list) -> dict[str, object]:
    if not rows:
        return {"count": 0, "net_pnl": 0.0, "winrate": 0.0, "profit_factor": 0.0}
    wins = [row for row in rows if (row.pnl_usd or 0) > 0]
    gross_profit = sum(row.pnl_usd for row in wins)
    gross_loss = abs(sum(row.pnl_usd for row in rows if (row.pnl_usd or 0) < 0))
    return {
        "count": len(rows),
        "net_pnl": sum(row.pnl_usd or 0 for row in rows),
        "winrate": len(wins) / len(rows) * 100,
        "profit_factor": gross_profit / gross_loss if gross_loss else gross_profit,
    }
