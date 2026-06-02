from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

from app.config import Settings
from app.data.models import SignalModel, SignalOutcomeModel
from app.data.repositories import _aware


@dataclass(frozen=True)
class HeatRow:
    symbol: str
    score: float
    price_change_5m_pct: float | None
    oi_change_15m_pct: float | None
    volume_spike_ratio: float
    spread_pct: float | None
    met: list[str]
    missing: list[str]


def card(title: str, body: str) -> str:
    return f"<b>{escape(title)}</b>\n\n{body.strip()}"


def kv(label: str, value: object) -> str:
    return f"<b>{escape(label)}:</b> {escape(_value(value))}"


def code_table(lines: list[str]) -> str:
    return "<pre>" + escape("\n".join(lines)) + "</pre>"


def bullet_list(items: list[str]) -> str:
    if not items:
        return "• n/a"
    return "\n".join(f"• {escape(item)}" for item in items)


def format_dashboard(
    online: bool,
    pairs_count: int,
    signals_today: int,
    signals_week: int,
    best_pattern: object,
    best_pair: object,
    uptime: timedelta,
    last_heartbeat: datetime | None = None,
    active_websocket_connections: int = 0,
    selected_symbols: list[str] | None = None,
    last_signal_time: datetime | None = None,
) -> str:
    selected_symbols = selected_symbols or []
    body = "\n".join(
        [
            kv("Status", "Online" if online else "Offline"),
            kv("Pairs", pairs_count),
            kv("Signals today", signals_today),
            kv("Signals week", signals_week),
            kv("WS connections", active_websocket_connections),
            kv("Last heartbeat", _time_or_na(last_heartbeat)),
            kv("Last signal", _time_or_na(last_signal_time)),
            "",
            "<b>Best pattern</b>",
            escape(_stat_row(best_pattern)),
            "",
            "<b>Best pair</b>",
            escape(_stat_row(best_pair)),
            "",
            "<b>Uptime</b>",
            escape(_format_uptime(uptime)),
            "",
            "<b>Selected symbols</b>",
            escape(_symbols_line(selected_symbols)),
        ]
    )
    return card("📊 Market Heat Radar", body)


def format_recent_signals(signals: list[SignalModel], page: int, page_size: int) -> str:
    if not signals:
        return card("📈 Recent Signals", "No signals yet.")
    blocks = []
    for signal in signals:
        ts = _aware(signal.timestamp).astimezone(UTC).strftime("%H:%M UTC")
        blocks.append(
            "\n".join(
                [
                    f"<b>{escape(signal.symbol)}</b>",
                    f"{escape(signal.direction)} • Score {signal.score}/10",
                    escape(ts),
                ]
            )
        )
    footer = f"Page {page + 1}"
    return card("📈 Recent Signals", "\n\n".join(blocks) + f"\n\n<code>{escape(footer)}</code>")


def format_signal_detail(signal: SignalModel) -> str:
    reasons = _json_list(signal.reasons_json)
    context = _json_dict(signal.market_context_json)
    outcome = _best_outcome(signal.outcomes)
    generated = _aware(signal.timestamp).astimezone(UTC).strftime("%H:%M UTC")
    metrics = [
        f"{'Price 5m':<16} {_pct(context.get('price_change_5m_pct'))}",
        f"{'OI 15m':<16} {_pct(context.get('oi_change_15m_pct'))}",
        f"{'Volume':<16} {_x(context.get('volume_spike_ratio'))}",
        f"{'Spread':<16} {_pct(context.get('spread_pct'))}",
        f"{'Funding':<16} {_pct(context.get('funding_rate_pct'))}",
        f"{'Local High':<16} {_number(context.get('swept_high_30m'))}",
        f"{'Local Low':<16} {_number(context.get('swept_low_30m'))}",
    ]
    body = "\n".join(
        [
            "<b>Pair</b>",
            escape(signal.symbol),
            "",
            "<b>Direction</b>",
            escape(signal.direction),
            "",
            "<b>Score</b>",
            f"{signal.score}/10",
            "",
            "<b>Reasons</b>",
            bullet_list(_compact_reasons(reasons)),
            "",
            "<b>Price</b>",
            escape(_price(signal.entry_price)),
            "",
            "<b>Generated</b>",
            escape(generated),
            "",
            "<b>Outcome</b>",
            escape(_format_outcome(outcome)),
            "",
            "<b>Score Breakdown</b>",
            bullet_list(_score_breakdown(context)),
            "",
            "<b>Market Context</b>",
            code_table(metrics),
        ]
    )
    return card("📈 Signal Details", body)


def format_signal_message(
    signal: SignalModel,
    reasons: list[str],
    context: dict[str, object],
    settings: Settings,
) -> str:
    header = "📈 Potential Setup"
    if signal.score >= settings.signals.strong_score:
        header = "⚠️ Potential Setup"
    body = "\n".join(
        [
            kv("Pair", signal.symbol),
            kv("Direction", signal.direction),
            kv("Score", f"{signal.score}/10"),
            "",
            "<b>Reasons</b>",
            bullet_list(_compact_reasons(reasons)),
            "",
            "<b>Entry ref</b>",
            escape(_price(signal.entry_price)),
            "",
            "<b>Invalidation</b>",
            escape(_signal_invalidation(signal.direction, context)),
        ]
    )
    return card(header, body)


def format_marked_entered(signal: SignalModel) -> str:
    entered_at = (
        _aware(signal.manual_entered_at or datetime.now(UTC)).astimezone(UTC).strftime("%H:%M UTC")
    )
    return card(
        "✓ Marked as entered",
        "\n".join(
            [
                kv("Pair", signal.symbol),
                kv("Entry", _price(signal.manual_entry_price or signal.entry_price)),
                kv("Time", entered_at),
            ]
        ),
    )


def format_ignored(signal: SignalModel) -> str:
    return card(
        "✓ Signal ignored",
        "\n".join(
            [
                kv("Pair", signal.symbol),
                kv("Status", "IGNORED"),
            ]
        ),
    )


def format_stats(summary: dict[str, object]) -> str:
    lines = [
        f"{'Total Signals':<14} {summary.get('total_signals', 0)}",
        f"{'Winrate':<14} {_pct(summary.get('winrate_tp1_30m'))}",
        f"{'Avg MFE':<14} {_pct(summary.get('avg_mfe_30m'))}",
        f"{'Avg MAE':<14} {_pct(summary.get('avg_mae_30m'))}",
        f"{'Best Pair':<14} {_stat_row(summary.get('best_pair'))}",
        f"{'Worst Pair':<14} {_stat_row(summary.get('worst_pair'))}",
        f"{'Best Pattern':<14} {_stat_row(summary.get('best_pattern'))}",
    ]
    return card("📉 Statistics", code_table(lines))


def format_scanner(rows: list[HeatRow]) -> str:
    if not rows:
        return card("📊 Heat Scanner", "No active market data yet.")
    table = [
        f"{idx:>2}. {row.symbol:<12} {row.score:>4.1f}" for idx, row in enumerate(rows, start=1)
    ]
    return card("📊 Heat Scanner", code_table(table))


def format_scanner_pair(row: HeatRow) -> str:
    metrics = [
        f"{'Score':<14} {row.score:.1f}",
        f"{'Price 5m':<14} {_pct(row.price_change_5m_pct)}",
        f"{'OI 15m':<14} {_pct(row.oi_change_15m_pct)}",
        f"{'Volume':<14} {row.volume_spike_ratio:.2f}x",
        f"{'Spread':<14} {_pct(row.spread_pct)}",
    ]
    body = "\n".join(
        [
            f"<b>{escape(row.symbol)}</b>",
            code_table(metrics),
            "<b>Met</b>",
            bullet_list(row.met),
            "",
            "<b>Missing</b>",
            bullet_list(row.missing),
        ]
    )
    return card("📊 Heat Details", body)


def format_settings(settings: Settings, paused: bool) -> str:
    notification_state = "On" if settings.telegram.notifications_enabled else "Off"
    auto_select = "On" if settings.symbols.auto_select else "Off"
    state = "Paused" if paused else "Active"
    lines = [
        f"{'Bot State':<18} {state}",
        f"{'Min Score':<18} {settings.signals.min_score}",
        f"{'Cooldown':<18} {settings.signals.cooldown_minutes_per_symbol}m",
        f"{'Auto Select':<18} {auto_select}",
        f"{'Max Symbols':<18} {settings.symbols.max_symbols}",
        f"{'Notifications':<18} {notification_state}",
    ]
    return card("⚙️ Settings", code_table(lines))


def format_paper_opened(trade, balance: float) -> str:
    body = "\n".join(
        [
            kv("Pair", trade.symbol),
            kv("Direction", trade.direction),
            "",
            code_table(
                [
                    f"{'Entry':<10} {_price(trade.entry_price)}",
                    f"{'Stop':<10} {_price(trade.stop_price)}",
                    f"{'Take':<10} {_price(trade.take_price)}",
                    f"{'Risk':<10} ${trade.risk_usd:.2f}",
                    f"{'Position':<10} ${trade.position_size_usd:.2f}",
                    f"{'Balance':<10} ${balance:.2f}",
                ]
            ),
        ]
    )
    return card("📈 Paper Trade Opened", body)


def format_paper_closed(trade, balance: float, winrate: float) -> str:
    result = trade.status.replace("CLOSED_", "")
    pnl = f"{trade.pnl_usd:+.2f}"
    body = "\n".join(
        [
            kv("Pair", trade.symbol),
            "",
            "<b>Result</b>",
            escape(result),
            "",
            "<b>PnL</b>",
            escape(f"${pnl}"),
            "",
            "<b>Balance</b>",
            escape(f"${balance:.2f}"),
            "",
            "<b>Winrate</b>",
            escape(f"{winrate:.1f}%"),
        ]
    )
    return card("📊 Paper Trade Closed", body)


def format_paper_portfolio(summary: dict[str, Any]) -> str:
    lines = [
        f"{'Balance':<16} ${summary.get('balance', 0):.2f}",
        f"{'Net Profit':<16} ${summary.get('net_profit', 0):+.2f}",
        f"{'Open Positions':<16} {summary.get('open_positions', 0)}",
        f"{'Trades':<16} {summary.get('trades', 0)}",
        f"{'Winrate':<16} {summary.get('winrate', 0):.1f}%",
        f"{'PF':<16} {summary.get('profit_factor', 0):.2f}",
        f"{'Expectancy':<16} {summary.get('expectancy_r', 0):+.2f}R",
        f"{'Avg Trade':<16} ${summary.get('average_trade', 0):+.2f}",
        f"{'Avg Winner':<16} ${summary.get('average_winner', 0):+.2f}",
        f"{'Avg Loser':<16} ${summary.get('average_loser', 0):+.2f}",
        f"{'Avg Hold':<16} {_duration(summary.get('average_holding_seconds', 0))}",
        f"{'Max DD':<16} {summary.get('max_drawdown_pct', 0):.2f}%",
        f"{'Win Streak':<16} {summary.get('max_consecutive_wins', 0)}",
        f"{'Loss Streak':<16} {summary.get('max_consecutive_losses', 0)}",
    ]
    return card("📊 Paper Portfolio", code_table(lines))


def format_config(settings: Settings) -> str:
    data = settings.model_dump()
    return card(
        "⚙️ Config", code_table(json.dumps(data, ensure_ascii=False, indent=2).splitlines()[:80])
    )


def since_today() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def since_week() -> datetime:
    return datetime.now(UTC) - timedelta(days=7)


def _value(value: object) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _price(value: float) -> str:
    return f"{value:.8g}"


def _pct(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _stat_row(row: object) -> str:
    if not row:
        return "n/a"
    try:
        name, wr = row[0], row[1]  # type: ignore[index]
        return f"{name} ({float(wr) * 100:.1f}%)"
    except Exception:
        return str(row)


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _json_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _compact_reasons(reasons: list[str]) -> list[str]:
    cleaned = []
    for reason in reasons:
        low = reason.lower()
        if "sweep low" in low:
            cleaned.append("Sweep Low")
        elif "sweep high" in low:
            cleaned.append("Sweep High")
        elif "oi" in low:
            cleaned.append("OI Growth")
        elif "volume" in low:
            cleaned.append("Volume Spike")
        elif "spread" in low:
            cleaned.append("Spread OK")
        elif "цена вернулась" in low:
            cleaned.append("Range Return")
        elif "price" in low:
            cleaned.append("Price Move")
        else:
            cleaned.append(reason)
    deduped: list[str] = []
    for item in cleaned:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _best_outcome(outcomes: list[SignalOutcomeModel]) -> SignalOutcomeModel | None:
    if not outcomes:
        return None
    return sorted(outcomes, key=lambda item: item.horizon_minutes)[-1]


def _format_outcome(outcome: SignalOutcomeModel | None) -> str:
    if outcome is None:
        return "Pending"
    return f"{outcome.horizon_minutes}m • MFE {outcome.mfe_pct:.2f}% • MAE {outcome.mae_pct:.2f}%"


def _signal_invalidation(direction: str, context: dict[str, Any]) -> str:
    if direction.upper() == "LONG":
        value = context.get("swept_low_30m")
        if value is not None:
            return _number(value)
        price = context.get("price")
        return _number(float(price) * 0.995) if price is not None else "n/a"
    value = context.get("swept_high_30m")
    if value is not None:
        return _number(value)
    price = context.get("price")
    return _number(float(price) * 1.005) if price is not None else "n/a"


def _score_breakdown(context: dict[str, Any]) -> list[str]:
    rows = []
    if (context.get("oi_change_15m_pct") or 0) > 0:
        rows.append("OI Growth")
    if context.get("swept_low_30m") is not None or context.get("swept_high_30m") is not None:
        rows.append("Sweep")
    if (context.get("volume_spike_ratio") or 0) >= 1:
        rows.append("Volume")
    if context.get("spread_pct") is not None:
        rows.append("Spread")
    if context.get("funding_rate_pct") is not None:
        rows.append("Funding")
    if context.get("price_change_5m_pct") is not None:
        rows.append("Price Move")
    return rows


def _number(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.8g}"
    except (TypeError, ValueError):
        return str(value)


def _x(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return "n/a"


def _format_uptime(value: timedelta) -> str:
    total = int(value.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _duration(seconds: object) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "n/a"
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _time_or_na(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return _aware(value).astimezone(UTC).strftime("%H:%M UTC")


def _symbols_line(symbols: list[str]) -> str:
    if not symbols:
        return "n/a"
    shown = symbols[:12]
    suffix = f" +{len(symbols) - len(shown)}" if len(symbols) > len(shown) else ""
    return ", ".join(shown) + suffix
