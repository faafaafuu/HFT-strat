from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import PaperAccountModel, PaperProfileModel, PaperTradeModel
from app.data.repositories import _aware


async def paper_summary(session: AsyncSession) -> dict[str, Any]:
    account = await session.scalar(
        select(PaperAccountModel).where(PaperAccountModel.name == "default")
    )
    trades = list(
        (
            await session.scalars(select(PaperTradeModel).where(PaperTradeModel.status != "OPEN"))
        ).all()
    )
    open_count = int(
        len(
            (
                await session.scalars(
                    select(PaperTradeModel).where(PaperTradeModel.status == "OPEN")
                )
            ).all()
        )
    )
    wins = [trade for trade in trades if trade.pnl_usd > 0]
    losses = [trade for trade in trades if trade.pnl_usd < 0]
    gross_profit = sum(trade.pnl_usd for trade in wins)
    gross_loss = abs(sum(trade.pnl_usd for trade in losses))
    total = len(trades)
    avg_trade = sum(trade.pnl_usd for trade in trades) / total if total else 0.0
    avg_winner = gross_profit / len(wins) if wins else 0.0
    avg_loser = -gross_loss / len(losses) if losses else 0.0
    avg_rr = sum(trade.realized_rr for trade in trades) / total if total else 0.0
    holding_times = [
        (
            _aware(trade.closed_at).astimezone(UTC) - _aware(trade.opened_at).astimezone(UTC)
        ).total_seconds()
        for trade in trades
        if trade.closed_at is not None
    ]
    return {
        "balance": account.balance if account else 0.0,
        "equity": account.equity if account else 0.0,
        "net_profit": account.net_profit if account else 0.0,
        "max_drawdown_pct": account.max_drawdown_pct if account else 0.0,
        "open_positions": open_count,
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "winrate": len(wins) / total * 100 if total else 0.0,
        "profit_factor": (
            gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0)
        ),
        "expectancy_r": avg_rr,
        "average_trade": avg_trade,
        "average_winner": avg_winner,
        "average_loser": avg_loser,
        "average_holding_seconds": (
            sum(holding_times) / len(holding_times) if holding_times else 0.0
        ),
        "max_consecutive_wins": _max_streak(trades, True),
        "max_consecutive_losses": _max_streak(trades, False),
        "avg_rr": avg_rr,
    }


async def paper_profiles_summary(session: AsyncSession) -> list[dict[str, Any]]:
    profiles = list(
        (
            await session.scalars(select(PaperProfileModel).order_by(PaperProfileModel.profile_key))
        ).all()
    )
    rows = []
    for profile in profiles:
        rows.append(await paper_profile_summary(session, profile.profile_key))
    return rows


async def paper_profile_summary(session: AsyncSession, profile_key: str) -> dict[str, Any]:
    profile = await session.scalar(
        select(PaperProfileModel).where(PaperProfileModel.profile_key == profile_key)
    )
    trades = list(
        (
            await session.scalars(
                select(PaperTradeModel).where(
                    PaperTradeModel.profile_key == profile_key,
                    PaperTradeModel.status != "OPEN",
                )
            )
        ).all()
    )
    open_trades = list(
        (
            await session.scalars(
                select(PaperTradeModel).where(
                    PaperTradeModel.profile_key == profile_key,
                    PaperTradeModel.status == "OPEN",
                )
            )
        ).all()
    )
    wins = [trade for trade in trades if trade.pnl_usd > 0]
    losses = [trade for trade in trades if trade.pnl_usd < 0]
    gross_profit = sum(trade.pnl_usd for trade in wins)
    gross_loss = abs(sum(trade.pnl_usd for trade in losses))
    total = len(trades)
    avg_trade = sum(trade.pnl_usd for trade in trades) / total if total else 0.0
    avg_rr = sum(trade.realized_rr for trade in trades) / total if total else 0.0
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_pnl = sum(
        trade.pnl_usd
        for trade in trades
        if trade.closed_at is not None and _aware(trade.closed_at).astimezone(UTC) >= today_start
    )
    settings = _settings(profile)
    balance = profile.current_balance if profile else 0.0
    initial = profile.initial_balance if profile else 0.0
    return {
        "profile_key": profile_key,
        "name": profile.name if profile else profile_key,
        "enabled": bool(profile.enabled) if profile else False,
        "balance": balance,
        "equity": profile.equity if profile else 0.0,
        "initial_balance": initial,
        "net_profit": profile.net_profit if profile else 0.0,
        "pnl_pct": ((balance - initial) / initial * 100) if initial else 0.0,
        "today_pnl": today_pnl,
        "open_positions": len(open_trades),
        "closed_trades": total,
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "winrate": len(wins) / total * 100 if total else 0.0,
        "profit_factor": (
            gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0)
        ),
        "expectancy_r": avg_rr,
        "average_trade": avg_trade,
        "max_drawdown_pct": profile.max_drawdown_pct if profile else 0.0,
        "settings": settings,
        "min_score": settings.get("min_score", 0),
        "risk_per_trade_pct": settings.get("risk_per_trade_pct", 0.0),
        "leverage": settings.get("leverage", 0.0),
    }


async def paper_profile_trades(
    session: AsyncSession,
    profile_key: str,
    status: str | None = None,
    limit: int = 10,
) -> list[PaperTradeModel]:
    filters = [PaperTradeModel.profile_key == profile_key]
    if status is not None:
        if status == "CLOSED":
            filters.append(PaperTradeModel.status != "OPEN")
        else:
            filters.append(PaperTradeModel.status == status)
    return list(
        (
            await session.scalars(
                select(PaperTradeModel)
                .where(*filters)
                .order_by(PaperTradeModel.opened_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def paper_breakdowns(session: AsyncSession) -> dict[str, list[tuple[str, float, int]]]:
    trades = list(
        (
            await session.scalars(select(PaperTradeModel).where(PaperTradeModel.status != "OPEN"))
        ).all()
    )
    return {
        "patterns": _group_winrate(trades, "pattern"),
        "symbols": _group_winrate(trades, "symbol"),
    }


def _group_winrate(trades: list[PaperTradeModel], attr: str) -> list[tuple[str, float, int]]:
    groups: dict[str, list[PaperTradeModel]] = {}
    for trade in trades:
        key = str(getattr(trade, attr) or "n/a")
        groups.setdefault(key, []).append(trade)
    rows = []
    for key, values in groups.items():
        wins = sum(1 for trade in values if trade.pnl_usd > 0)
        rows.append((key, wins / len(values) * 100 if values else 0.0, len(values)))
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows


def _max_streak(trades: list[PaperTradeModel], wins: bool) -> int:
    current = 0
    best = 0
    for trade in sorted(trades, key=lambda item: item.closed_at or item.opened_at):
        is_win = trade.pnl_usd > 0
        if is_win == wins:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _settings(profile: PaperProfileModel | None) -> dict[str, Any]:
    if profile is None or not profile.settings_json:
        return {}
    import json

    try:
        value = json.loads(profile.settings_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
