from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.data.models import PaperProfileModel
from app.data.repositories import HyperoptCacheRepository, RuntimeSettingsRepository
from app.jobs.models import (
    DOWNLOAD_HISTORY,
    RUN_BACKTEST,
    RUN_DENSITY_ANALYSIS,
    RUN_HYPEROPT,
    TRAIN_ML_MODEL,
)
from app.jobs.queue import JobQueue
from app.web.auth import require_web_auth

router = APIRouter(prefix="/actions", dependencies=[Depends(require_web_auth)])

_JOB_LABELS = {
    DOWNLOAD_HISTORY: "Загрузка истории",
    RUN_BACKTEST: "Бэктест",
    RUN_HYPEROPT: "Гипероптимизация",
    TRAIN_ML_MODEL: "Обучение ML-модели",
    RUN_DENSITY_ANALYSIS: "Анализ плотностей",
}


async def _persist(request: Request, key: str, value: Any) -> None:
    async with request.app.state.database.session() as session:
        await RuntimeSettingsRepository(session).set(key, value)


def _toast(request: Request, message: str, tone: str = "ok") -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, "partials/toast.html", {"message": message, "tone": tone}
    )


@router.post("/strategies/{strategy_key}/toggle", response_class=HTMLResponse)
async def toggle_strategy(request: Request, strategy_key: str):
    settings = request.app.state.settings
    toggles = settings.strategy_toggles
    enabled = not toggles.is_enabled(strategy_key)
    toggles.set_enabled(strategy_key, enabled)
    await _persist(request, "strategy_toggles.disabled", toggles.disabled)
    request.app.state.strategy_lab_service.invalidate_cache()
    state = "включена" if enabled else "выключена"
    return await _render_after_toggle(
        request, "strategies", f"Стратегия {strategy_key} {state}"
    )


@router.post("/strategy-profiles/{profile_key}/toggle", response_class=HTMLResponse)
async def toggle_strategy_profile(request: Request, profile_key: str):
    settings = request.app.state.settings
    profile = settings.strategy_profiles.profiles.get(profile_key)
    if profile is None:
        return _toast(request, f"Профиль {profile_key} не найден", tone="error")
    profile.enabled = not profile.enabled
    await _persist(request, f"strategy_profiles.profiles.{profile_key}.enabled", profile.enabled)
    request.app.state.strategy_lab_service.invalidate_cache()
    state = "включён" if profile.enabled else "выключен"
    return await _render_after_toggle(
        request, "strategies", f"Профиль {profile_key} {state}"
    )


@router.post("/instances/{instance_id}/toggle", response_class=HTMLResponse)
async def toggle_instance(request: Request, instance_id: str):
    settings = request.app.state.settings
    instance = settings.strategy_instances.instances.get(instance_id)
    if instance is None:
        return _toast(request, f"Пресет {instance_id} не найден", tone="error")
    instance.enabled = not instance.enabled
    await _persist(
        request, f"strategy_instances.instances.{instance_id}.enabled", instance.enabled
    )
    request.app.state.strategy_lab_service.invalidate_cache()
    state = "включён" if instance.enabled else "выключен"
    return await _render_after_toggle(request, "instances", f"Пресет {instance_id} {state}")


async def _render_after_toggle(request: Request, lab_section: str, message: str) -> HTMLResponse:
    """Toggles live on both the Paper page and the Lab, so answer with the caller's fragment."""
    params = await _form_params(request)
    if params.get("return_to") == "paper":
        return await _render_paper(request, message=message)
    if lab_section == "instances":
        return await _render_instances(request, message=message)
    return await _render_strategies(request, message=message)


@router.post("/instances/{instance_id}/settings", response_class=HTMLResponse)
async def update_instance(request: Request, instance_id: str):
    settings = request.app.state.settings
    instance = settings.strategy_instances.instances.get(instance_id)
    if instance is None:
        return _toast(request, f"Пресет {instance_id} не найден", tone="error")
    params = await _form_params(request)
    applied: list[str] = []
    for key, raw in params.items():
        value = _coerce(raw)
        if key == "min_score":
            instance.min_score = int(str(value))
        elif key == "paper_profile":
            instance.paper_profile = str(value)
        elif key.startswith("config."):
            if str(raw).strip() == "":
                continue
            instance.config[key.removeprefix("config.")] = value
        else:
            continue
        await _persist(request, f"strategy_instances.instances.{instance_id}.{key}", value)
        applied.append(key)
    if not applied:
        return _toast(request, "Нечего сохранять", tone="warn")
    return await _render_instances(
        request, message=f"Пресет {instance_id}: сохранено {len(applied)} параметров"
    )


@router.post("/paper-profiles/{profile_key}/toggle", response_class=HTMLResponse)
async def toggle_paper_profile(request: Request, profile_key: str):
    settings = request.app.state.settings
    profile = settings.paper.profiles.get(profile_key)
    if profile is None:
        return _toast(request, f"Paper-профиль {profile_key} не найден", tone="error")
    profile.enabled = not profile.enabled
    await _persist(request, f"paper.profiles.{profile_key}.enabled", profile.enabled)
    async with request.app.state.database.session() as session:
        row = (
            await session.scalars(
                select(PaperProfileModel).where(PaperProfileModel.profile_key == profile_key)
            )
        ).first()
        if row is not None:
            row.enabled = profile.enabled
    return await _render_paper(request)


@router.post("/paper/toggle", response_class=HTMLResponse)
async def toggle_paper_trading(request: Request):
    settings = request.app.state.settings
    settings.paper.enabled = not settings.paper.enabled
    await _persist(request, "paper.enabled", settings.paper.enabled)
    return await _render_paper(request)


@router.post("/hyperopt/cache/clear", response_class=HTMLResponse)
async def clear_hyperopt_cache(request: Request):
    params = await _form_params(request)
    strategy_key = str(params.get("strategy_key") or "") or None
    async with request.app.state.database.session() as session:
        removed = await HyperoptCacheRepository(session).clear(strategy_key)
    request.app.state.strategy_lab_service.invalidate_cache()
    scope = f" для {strategy_key}" if strategy_key else ""
    return _toast(request, f"Кеш очищен{scope}: удалено комбинаций — {removed}")


@router.post("/hyperopt/apply", response_class=HTMLResponse)
async def apply_hyperopt_row(request: Request):
    """Copy one sweep row's parameters into a strategy instance."""
    params = await _form_params(request)
    instance_id = str(params.get("instance_id") or "")
    instance = request.app.state.settings.strategy_instances.instances.get(instance_id)
    if instance is None:
        return _toast(request, f"Пресет {instance_id} не найден", tone="error")
    sweep = await request.app.state.strategy_lab_service.hyperopt_results()
    rows = sweep.get("rows") or []
    try:
        row = rows[int(str(params.get("row", 0)))]
    except (ValueError, IndexError):
        return _toast(request, "Строка перебора не найдена", tone="error")
    if sweep.get("strategy_key") and sweep["strategy_key"] != instance.strategy_key:
        return _toast(
            request,
            f"Перебор считался для {sweep['strategy_key']}, "
            f"а пресет работает на {instance.strategy_key} — параметры не подойдут",
            tone="error",
        )
    applied = dict(row.get("params") or {})
    async with request.app.state.database.session() as session:
        repo = RuntimeSettingsRepository(session)
        for key, value in applied.items():
            instance.config[key] = value
            await repo.set(f"strategy_instances.instances.{instance_id}.config.{key}", value)
    request.app.state.strategy_lab_service.invalidate_cache()
    listing = ", ".join(f"{key}={value}" for key, value in applied.items())
    return _toast(request, f"Пресет {instance_id} обновлён: {listing}")


@router.post("/jobs/{job_type}", response_class=HTMLResponse)
async def enqueue_job(request: Request, job_type: str):
    known = {
        "history": DOWNLOAD_HISTORY,
        "backtest": RUN_BACKTEST,
        "hyperopt": RUN_HYPEROPT,
        "ml": TRAIN_ML_MODEL,
        "density": RUN_DENSITY_ANALYSIS,
    }
    resolved = known.get(job_type)
    if resolved is None:
        return _toast(request, "Неизвестный тип задачи", tone="error")
    params = await _form_params(request)
    payload = _job_payload(request, resolved, params)
    job = await JobQueue(request.app.state.database).enqueue(resolved, payload)
    label = _JOB_LABELS.get(resolved, resolved)
    return request.app.state.templates.TemplateResponse(
        request,
        "partials/job_started.html",
        {
            "message": f"{label}: задача #{job['id']} поставлена в очередь",
            "job": job,
            "payload": payload,
        },
    )


def _job_payload(request: Request, job_type: str, params: dict[str, Any]) -> dict[str, Any]:
    symbol = str(params.get("symbol", "BTCUSDT")).upper()
    # Hyperopt accepts a comma-separated list and sweeps each timeframe separately.
    checked = params.get("timeframes")
    timeframe = ",".join(checked) if isinstance(checked, list) else str(
        checked or params.get("timeframe") or "5m"
    )
    days = int(str(params.get("days") or 30))
    if job_type == DOWNLOAD_HISTORY:
        return {"symbol": symbol, "timeframe": timeframe, "days": days}
    if job_type == TRAIN_ML_MODEL:
        return {"model_type": str(params.get("model_type", "heuristic_gbdt_proxy"))}
    if job_type == RUN_DENSITY_ANALYSIS:
        return {"symbol": params.get("symbol") or None, "limit": 500}

    instance_id = str(params.get("strategy_instance_id") or "")
    strategy_key = str(params.get("strategy_key") or "micro_stop_hunt_reclaim")
    instance_params: dict[str, Any] = {}
    if instance_id:
        instance = request.app.state.settings.strategy_instances.instances.get(instance_id)
        if instance is not None:
            strategy_key = instance.strategy_key
            instance_params = dict(instance.config)
            instance_params.setdefault("min_score", instance.min_score)
    # Without an explicit threshold the engine falls back to the live signals.min_score,
    # which is tuned to keep Telegram quiet and silently empties every backtest.
    if params.get("min_score"):
        instance_params["min_score"] = int(str(params["min_score"]))
    payload: dict[str, Any] = {
        "strategy_key": strategy_key,
        "symbol": symbol,
        "timeframe": timeframe,
        "days": days,
        "params": instance_params,
        "strategy_instance_id": instance_id or None,
    }
    if job_type == RUN_HYPEROPT and params.get("limit"):
        payload["limit"] = int(str(params["limit"]))
    return payload


async def _render_strategies(request: Request, message: str | None = None) -> HTMLResponse:
    lab = request.app.state.strategy_lab_service
    return request.app.state.templates.TemplateResponse(
        request,
        "lab/strategies.html",
        {
            "lab": {"strategies": await lab.strategies(), "profiles": await lab.strategy_profiles()},
            "section": "strategies",
            "message": message,
        },
    )


async def _render_instances(request: Request, message: str | None = None) -> HTMLResponse:
    lab = request.app.state.strategy_lab_service
    return request.app.state.templates.TemplateResponse(
        request,
        "lab/instances.html",
        {
            "lab": {"instances": await lab.instances(), "strategies": await lab.strategies()},
            "section": "instances",
            "message": message,
        },
    )


async def _render_paper(request: Request, message: str | None = None) -> HTMLResponse:
    lab = request.app.state.strategy_lab_service
    return request.app.state.templates.TemplateResponse(
        request,
        "partials/paper_controls.html",
        {
            "profiles": await request.app.state.paper_service.profiles(),
            "settings": request.app.state.settings,
            "instances": await lab.instances(),
            "strategy_profiles": await lab.strategy_profiles(),
            "strategies": await lab.strategies(),
            "message": message,
        },
    )


_MULTI_VALUE_FIELDS = {"timeframes"}


async def _form_params(request: Request) -> dict[str, Any]:
    raw = (await request.body()).decode()
    parsed: dict[str, Any] = {
        key: values if key in _MULTI_VALUE_FIELDS else values[0]
        for key, values in parse_qs(raw).items()
    }
    parsed.update(dict(request.query_params))
    return parsed


def _coerce(value: object) -> object:
    text = str(value).strip()
    if text.lower() in {"true", "on", "yes"}:
        return True
    if text.lower() in {"false", "off", "no"}:
        return False
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text
