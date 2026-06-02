from __future__ import annotations

from datetime import UTC

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.config import save_settings
from app.data.repositories import SignalRepository
from app.market.features import FeatureSnapshot
from app.signals.patterns import detect_patterns
from app.signals.scoring import score_signal
from app.telegram.formatters import (
    HeatRow,
    format_config,
    format_dashboard,
    format_ignored,
    format_marked_entered,
    format_paper_portfolio,
    format_recent_signals,
    format_scanner,
    format_scanner_pair,
    format_settings,
    format_signal_detail,
    format_stats,
    since_today,
    since_week,
)
from app.telegram.keyboards import (
    main_menu,
    nav,
    scanner_menu,
    scanner_pair_menu,
    settings_menu,
    signal_alert_detail_menu,
    signal_alert_menu,
    signal_detail_menu,
    signals_menu,
)
from app.paper.statistics import paper_summary
from app.utils.time import utc_now


SIGNALS_PAGE_SIZE = 6


class TelegramCommands:
    def __init__(self, service: "TelegramService") -> None:
        self.service = service

    async def start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_or_edit(update, "Select a section.", main_menu(), title=True)

    async def help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_or_edit(update, "Select a section.", main_menu(), title=True)

    async def status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self.show_dashboard(update)

    async def signals(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self.show_signals(update, page=0)

    async def stats(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self.show_stats(update)

    async def stats_today(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self.show_stats(update, since=since_today().replace(tzinfo=UTC))

    async def stats_week(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self.show_stats(update, since=since_week().replace(tzinfo=UTC))

    async def top_pairs(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self.show_stats(update)

    async def top_patterns(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self.show_stats(update)

    async def paper(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self.show_paper(update)

    async def config(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_or_edit(update, format_config(self.service.settings), nav("config"))

    async def pause(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        self.service.paused = True
        await self.show_settings(update)

    async def resume(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        self.service.paused = False
        await self.show_settings(update)

    async def callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.data is None:
            return
        await query.answer()
        data = query.data
        if data == "home":
            await self._send_or_edit(update, "Select a section.", main_menu(), title=True)
        elif data == "dashboard":
            await self.show_dashboard(update)
        elif data.startswith("signals:"):
            await self.show_signals(update, page=_int_part(data, 1, 0))
        elif data.startswith("signal:"):
            parts = data.split(":")
            signal_id = int(parts[1])
            page = int(parts[2]) if len(parts) > 2 else 0
            await self.show_signal_detail(update, signal_id, page)
        elif data.startswith("signal_details:"):
            await self.show_signal_alert_detail(update, int(data.split(":", 1)[1]))
        elif data.startswith("signal_enter:"):
            await self.mark_signal_entered(update, int(data.split(":", 1)[1]))
        elif data.startswith("signal_ignore:"):
            await self.ignore_signal(update, int(data.split(":", 1)[1]))
        elif data == "stats":
            await self.show_stats(update)
        elif data == "config":
            await self._send_or_edit(update, format_config(self.service.settings), nav("config"))
        elif data == "scanner":
            await self.show_scanner(update)
        elif data.startswith("scanner_pair:"):
            await self.show_scanner_pair(update, data.split(":", 1)[1])
        elif data == "paper":
            await self.show_paper(update)
        elif data == "settings":
            await self.show_settings(update)
        elif data.startswith("settings:"):
            await self.apply_setting(data)
            await self.show_settings(update)

    async def show_dashboard(self, update: Update) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            today_count = await repo.count_since(since_today().replace(tzinfo=UTC))
            week_count = await repo.count_since(since_week().replace(tzinfo=UTC))
            summary = await repo.summary()
            last_signal_time = await repo.last_signal_time()
        text = format_dashboard(
            online=self.service.is_online,
            pairs_count=len(self.service.symbols),
            signals_today=today_count,
            signals_week=week_count,
            best_pattern=summary.get("best_pattern"),
            best_pair=summary.get("best_pair"),
            uptime=utc_now() - self.service.started_at,
            last_heartbeat=self.service.last_heartbeat,
            active_websocket_connections=self.service.active_websocket_connections,
            selected_symbols=self.service.symbols,
            last_signal_time=last_signal_time,
        )
        await self._send_or_edit(update, text, nav("dashboard"))

    async def show_signals(self, update: Update, page: int) -> None:
        page = max(0, page)
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signals = await repo.list_recent(limit=SIGNALS_PAGE_SIZE, offset=page * SIGNALS_PAGE_SIZE)
        await self._send_or_edit(
            update,
            format_recent_signals(signals, page, SIGNALS_PAGE_SIZE),
            signals_menu(signals, page, SIGNALS_PAGE_SIZE),
        )

    async def show_signal_detail(self, update: Update, signal_id: int, page: int) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signal = await repo.get_signal_with_outcomes(signal_id)
        if signal is None:
            await self._send_or_edit(update, "Signal not found.", signal_detail_menu(page, signal_id))
            return
        await self._send_or_edit(update, format_signal_detail(signal), signal_detail_menu(page, signal_id))

    async def show_signal_alert_detail(self, update: Update, signal_id: int) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signal = await repo.get_signal_with_outcomes(signal_id)
        if signal is None:
            await self._send_or_edit(update, "Signal not found.", nav("home"))
            return
        await self._send_or_edit(update, format_signal_detail(signal), signal_alert_detail_menu(signal))

    async def mark_signal_entered(self, update: Update, signal_id: int) -> None:
        now = utc_now()
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signal = await repo.get_signal(signal_id)
            if signal is None:
                await self._send_or_edit(update, "Signal not found.", nav("home"))
                return
            price = self._manual_entry_price(signal.exchange, signal.symbol, signal.entry_price)
            signal = await repo.mark_entered_manual(signal_id, price, now)
        if signal is None:
            await self._send_or_edit(update, "Signal not found.", nav("home"))
            return
        await self._send_or_edit(update, format_marked_entered(signal), signal_alert_menu(signal))

    async def ignore_signal(self, update: Update, signal_id: int) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signal = await repo.ignore_signal(signal_id)
        if signal is None:
            await self._send_or_edit(update, "Signal not found.", nav("home"))
            return
        await self._send_or_edit(update, format_ignored(signal), signal_alert_menu(signal))

    async def show_stats(self, update: Update, since=None) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            summary = await repo.summary(since=since)
        await self._send_or_edit(update, format_stats(summary), nav("stats"))

    async def show_scanner(self, update: Update) -> None:
        rows = self._heat_rows()
        await self._send_or_edit(update, format_scanner(rows), scanner_menu(rows))

    async def show_scanner_pair(self, update: Update, symbol: str) -> None:
        rows = {row.symbol: row for row in self._heat_rows(limit=50)}
        row = rows.get(symbol)
        if row is None:
            row = HeatRow(
                symbol=symbol,
                score=0,
                price_change_5m_pct=None,
                oi_change_15m_pct=None,
                volume_spike_ratio=0,
                spread_pct=None,
                met=[],
                missing=["No active data"],
            )
        await self._send_or_edit(update, format_scanner_pair(row), scanner_pair_menu(symbol))

    async def show_settings(self, update: Update) -> None:
        await self._send_or_edit(update, format_settings(self.service.settings, self.service.paused), settings_menu())

    async def show_paper(self, update: Update) -> None:
        async with self.service.database.session() as session:
            summary = await paper_summary(session)
        await self._send_or_edit(update, format_paper_portfolio(summary), nav("paper"))

    async def apply_setting(self, data: str) -> None:
        parts = data.split(":")
        if len(parts) < 3:
            return
        action = parts[1]
        key = parts[2]
        if action == "toggle":
            if key == "auto_select":
                self.service.settings.symbols.auto_select = not self.service.settings.symbols.auto_select
            elif key == "notifications":
                self.service.settings.telegram.notifications_enabled = (
                    not self.service.settings.telegram.notifications_enabled
                )
        elif action == "set" and len(parts) == 4:
            delta = int(parts[3])
            if key == "min_score":
                self.service.settings.signals.min_score = min(
                    10,
                    max(1, self.service.settings.signals.min_score + delta),
                )
            elif key == "cooldown":
                self.service.settings.signals.cooldown_minutes_per_symbol = max(
                    0,
                    self.service.settings.signals.cooldown_minutes_per_symbol + delta,
                )
            elif key == "max_symbols":
                self.service.settings.symbols.max_symbols = min(
                    200,
                    max(1, self.service.settings.symbols.max_symbols + delta),
                )
        save_settings(self.service.settings, self.service.config_path)

    def _heat_rows(self, limit: int = 10) -> list[HeatRow]:
        if self.service.feature_store is None:
            return []
        rows: list[HeatRow] = []
        for symbol in self.service.symbols:
            snapshot = self.service.feature_store.snapshot(
                "bybit",
                symbol,
                sweep_lookback_minutes=self.service.settings.thresholds.sweep_lookback_minutes,
                sweep_return_minutes=self.service.settings.thresholds.sweep_return_minutes,
            )
            if snapshot is None:
                continue
            rows.append(self._heat_row(snapshot))
        rows.sort(key=lambda item: item.score, reverse=True)
        return rows[:limit]

    def _heat_row(self, snapshot: FeatureSnapshot) -> HeatRow:
        thresholds = self.service.settings.thresholds
        met: list[str] = []
        missing: list[str] = []
        score = 0.0

        price_change = snapshot.price_change_5m_pct
        if price_change is not None and abs(price_change) >= thresholds.price_change_5m_pct:
            met.append("Price Move")
            score += min(2.0, abs(price_change) / thresholds.price_change_5m_pct * 1.5)
        else:
            missing.append("Price Move")

        oi_change = snapshot.oi_change_15m_pct
        if oi_change is not None and oi_change >= thresholds.oi_change_15m_pct:
            met.append("OI Growth")
            score += min(2.0, oi_change / thresholds.oi_change_15m_pct * 1.5)
        else:
            missing.append("OI Growth")

        if snapshot.volume_spike_ratio >= thresholds.volume_spike_multiplier:
            met.append("Volume Spike")
            score += min(2.0, snapshot.volume_spike_ratio / thresholds.volume_spike_multiplier * 1.5)
        else:
            missing.append("Volume Spike")

        if snapshot.spread_pct is not None and snapshot.spread_pct <= self.service.settings.symbols.max_spread_pct:
            met.append("Spread OK")
            score += 1.5
        else:
            missing.append("Spread OK")

        if snapshot.returned_after_low_sweep or snapshot.returned_after_high_sweep:
            met.append("Sweep Return")
            score += 2.5
        else:
            missing.append("Sweep Return")

        for candidate in detect_patterns(snapshot, thresholds):
            score = max(score, float(score_signal(candidate, thresholds)))

        return HeatRow(
            symbol=snapshot.symbol,
            score=min(10.0, score),
            price_change_5m_pct=price_change,
            oi_change_15m_pct=oi_change,
            volume_spike_ratio=snapshot.volume_spike_ratio,
            spread_pct=snapshot.spread_pct,
            met=met,
            missing=missing,
        )

    def _manual_entry_price(self, exchange: str, symbol: str, fallback: float) -> float:
        if self.service.feature_store is None:
            return fallback
        return self.service.feature_store.latest_price(exchange, symbol) or fallback

    async def _send_or_edit(
        self,
        update: Update,
        text: str,
        reply_markup: InlineKeyboardMarkup,
        title: bool = False,
    ) -> None:
        if title:
            text = "<b>📊 Market Heat Radar</b>\n\n" + text
        if update.callback_query and update.callback_query.message:
            try:
                await update.callback_query.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except BadRequest as exc:
                if "Message is not modified" not in str(exc):
                    raise
            return
        if update.message:
            await update.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )


def _int_part(data: str, index: int, default: int) -> int:
    try:
        return int(data.split(":")[index])
    except (IndexError, ValueError):
        return default


from app.telegram.bot import TelegramService  # noqa: E402
